"""“Connect with Google”: send and read through the Gmail API instead of app passwords.

This is the sign-in popular websites use (OAuth 2.0).  Because the Radar runs on your own
computer, it uses an OAuth client that you create once in your own Google Cloud project
(type “Desktop app”); its ID and secret are pasted in Settings.  Each mailbox is then
connected through Google's usual consent screen, with PKCE, and Google returns a refresh
token that is stored encrypted for your Windows user (DPAPI), like the app passwords were.

* Sending uses only the ``gmail.send`` permission.  Reading replies (``gmail.readonly``) is
  optional, and the Radar still opens only the matched messages described in ``inbox``.
* Everything goes over HTTPS (port 443), so networks that block mail ports do not matter.
* Google signs the browser back in to ``http://127.0.0.1:<port>/`` — the loopback address
  Google allows for desktop apps — so nothing is exposed outside this computer.
"""

from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import json
import secrets
import threading
import time
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from . import db, mailer

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send?uploadType=media"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
BASE_SCOPES = ("openid", "email")
PENDING_SECONDS = 900

_pending: dict[str, dict[str, Any]] = {}            # state -> sign-in in progress
_tokens: dict[str, tuple[str, float]] = {}           # address -> (access token, expiry)
_lock = threading.Lock()


class GoogleError(mailer.MailError):
    pass


# ------------------------------------------------------------------ client
def client() -> Optional[dict[str, str]]:
    stored = db.get_setting("google_client") or {}
    if not stored.get("client_id"):
        return None
    return {"client_id": stored["client_id"], "client_secret": mailer.reveal(stored.get("secret") or "")}


def client_status() -> dict[str, Any]:
    stored = db.get_setting("google_client") or {}
    client_id = stored.get("client_id") or ""
    return {"configured": bool(client_id), "client_id": client_id, "project": stored.get("project") or ""}


def set_client(raw: str = "", client_id: str = "", client_secret: str = "") -> dict[str, Any]:
    """Store the OAuth client, from the JSON file Google lets you download or from the two values."""
    project = ""
    if raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GoogleError("This is not the JSON file Google gave you (Download JSON on the client's page).") from exc
        if not isinstance(data, dict):
            raise GoogleError("This is not the JSON file Google gave you.")
        if "web" in data:
            raise GoogleError("This client is of type “Web application”. Create one of type “Desktop app” and load its file.")
        block = data.get("installed") or data
        client_id, client_secret = str(block.get("client_id") or ""), str(block.get("client_secret") or "")
        project = str(block.get("project_id") or "")
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise GoogleError("The client ID should end with “.apps.googleusercontent.com”.")
    if not client_secret:
        raise GoogleError("The client secret is missing.")
    db.set_setting("google_client", {"client_id": client_id, "secret": mailer.protect(client_secret), "project": project})
    return client_status()


def remove_client() -> dict[str, Any]:
    db.set_setting("google_client", {})
    return client_status()


# --------------------------------------------------------------- accounts
def connected(account: dict[str, Any]) -> bool:
    return bool((account.get("oauth") or {}).get("refresh"))


def can_read(account: dict[str, Any]) -> bool:
    return connected(account) and READ_SCOPE in (account.get("oauth") or {}).get("scopes", [])


def public(account: dict[str, Any]) -> dict[str, Any]:
    oauth = account.get("oauth") or {}
    return {"connected": connected(account), "read": can_read(account), "expired": bool(oauth.get("expired")),
            "connected_at": oauth.get("connected_at") or ""}


def _save_account(index: int, update) -> dict[str, Any]:
    current = mailer.settings()
    update(current["accounts"][index])
    db.set_setting("mail", current)
    return current["accounts"][index]


def _slot_for(address: str, wanted: int) -> int:
    """The account slot a newly connected address belongs to."""
    accounts = mailer.settings()["accounts"]
    for index, account in enumerate(accounts):
        if (account.get("address") or "").lower() == address:
            return index
    return wanted


# ----------------------------------------------------------------- sign-in
def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def start(index: int, read: bool, port: int) -> str:
    """The Google consent-screen address for account slot ``index``."""
    conf = client()
    if not conf:
        raise GoogleError("Add your Google client first (step 1 in Settings › E-mail sending).")
    accounts = mailer.settings()["accounts"]
    if not 0 <= index < len(accounts):
        raise GoogleError("Unknown account slot.")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    redirect = f"http://127.0.0.1:{int(port)}/"
    with _lock:
        now = time.time()
        for key in [key for key, value in _pending.items() if now - value["created"] > PENDING_SECONDS]:
            _pending.pop(key, None)
        _pending[state] = {"verifier": verifier, "redirect": redirect, "index": index, "created": now}
    params = {
        "client_id": conf["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join([*BASE_SCOPES, SEND_SCOPE, *([READ_SCOPE] if read else [])]),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if accounts[index].get("address"):
        params["login_hint"] = accounts[index]["address"]
    return f"{AUTH_URL}?{urlencode(params)}"


def is_callback(query: dict[str, list[str]]) -> bool:
    return bool(query.get("state")) and bool(query.get("code") or query.get("error"))


_REFUSALS = {
    "access_denied": "The sign-in was cancelled, or your organisation does not allow this app. If Google showed “Access blocked” or "
                     "“admin_policy_enforced”, your school administrator restricts third-party apps: connect with “Also read replies” "
                     "unticked (sending only needs less), or ask the IT service to allow your client ID.",
    "admin_policy_enforced": "Your school administrator does not allow third-party apps to use this account's Gmail. Ask the IT service "
                             "to trust your client ID, or keep sending from your personal Gmail.",
    "org_internal": "This Google client only accepts accounts of its own organisation. In Google Cloud, set the audience to “External”.",
}


def _claims(id_token: str) -> dict[str, Any]:
    """Claims of the ID token received directly from Google's token endpoint over HTTPS."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def _post(url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    try:
        response = requests.post(url, data=data, timeout=25)
    except requests.RequestException as exc:
        raise GoogleError(f"Could not reach Google ({type(exc).__name__}). Check the internet connection and try again.") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def finish(query: dict[str, list[str]]) -> dict[str, Any]:
    """Complete the sign-in Google redirected back to us; returns the connected address."""
    state = (query.get("state") or [""])[0]
    with _lock:
        pending = _pending.pop(state, None)
    if not pending or time.time() - pending["created"] > PENDING_SECONDS:
        raise GoogleError("This sign-in link has expired or was already used. Start again from Settings.")
    if query.get("error"):
        error = query["error"][0]
        raise GoogleError(_REFUSALS.get(error, f"Google refused the sign-in ({error})."))
    conf = client()
    if not conf:
        raise GoogleError("The Google client was removed during the sign-in.")
    status, data = _post(TOKEN_URL, {
        "code": (query.get("code") or [""])[0], "client_id": conf["client_id"], "client_secret": conf["client_secret"],
        "redirect_uri": pending["redirect"], "grant_type": "authorization_code", "code_verifier": pending["verifier"]})
    if status != 200:
        raise GoogleError(f"Google refused the sign-in: {data.get('error_description') or data.get('error') or status}.")
    address = str(_claims(data.get("id_token") or "").get("email") or "").lower()
    granted = sorted(set(str(data.get("scope") or "").split()))
    if not address:
        raise GoogleError("Google did not say which address was connected. Try again.")
    if SEND_SCOPE not in granted:
        raise GoogleError("The permission “Send email on your behalf” was not ticked on Google's screen. Connect again and tick it.")
    if not data.get("refresh_token"):
        raise GoogleError("Google did not give a lasting authorisation. Remove “Internship Radar” at myaccount.google.com/permissions, "
                          "then connect again.")
    index = _slot_for(address, pending["index"])
    secret = mailer.protect(data["refresh_token"])

    def apply(account: dict[str, Any]) -> None:
        account["address"] = address
        account["oauth"] = {"refresh": secret, "scopes": granted, "connected_at": db.now(), "expired": False}

    _save_account(index, apply)
    with _lock:
        _tokens[address] = (str(data.get("access_token") or ""), time.time() + int(data.get("expires_in") or 3600) - 90)
    return {"address": address, "read": READ_SCOPE in granted, "index": index}


def disconnect(index: int) -> dict[str, Any]:
    account = mailer.settings()["accounts"][index]
    oauth = account.get("oauth") or {}
    if oauth.get("refresh"):
        try:  # withdraw the authorisation at Google too; the local copy is deleted either way
            requests.post(REVOKE_URL, data={"token": mailer.reveal(oauth["refresh"])}, timeout=15)
        except (requests.RequestException, mailer.MailError):
            pass
    with _lock:
        _tokens.pop((account.get("address") or "").lower(), None)
    _save_account(index, lambda account: account.pop("oauth", None))
    return mailer.public_settings()


# ------------------------------------------------------------------ tokens
def _mark_expired(address: str) -> None:
    accounts = mailer.settings()["accounts"]
    for index, account in enumerate(accounts):
        if (account.get("address") or "").lower() == address and account.get("oauth"):
            _save_account(index, lambda item: item["oauth"].update(expired=True))


def access_token(account: dict[str, Any], fresh: bool = False) -> str:
    address = (account.get("address") or "").lower()
    with _lock:
        cached = _tokens.get(address)
    if cached and cached[0] and cached[1] > time.time() and not fresh:
        return cached[0]
    conf = client()
    if not conf:
        raise GoogleError(f"{address}: the Google client is missing (Settings › E-mail sending).")
    status, data = _post(TOKEN_URL, {"client_id": conf["client_id"], "client_secret": conf["client_secret"],
                                     "refresh_token": mailer.reveal(account["oauth"]["refresh"]), "grant_type": "refresh_token"})
    if status != 200:
        if data.get("error") in ("invalid_grant", "unauthorized_client"):
            _mark_expired(address)
            raise GoogleError(f"{address}: the Google authorisation has expired or was withdrawn — click “Connect with Google” again "
                              "in Settings. (If your Google Cloud app is still in “Testing”, publish it so this stops happening weekly.)")
        raise GoogleError(f"{address}: Google refused to renew access ({data.get('error_description') or data.get('error') or status}).")
    token = str(data.get("access_token") or "")
    with _lock:
        _tokens[address] = (token, time.time() + int(data.get("expires_in") or 3600) - 90)
    return token


def _api_error(response: requests.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return f"HTTP {response.status_code}"
    message = error.get("message") if isinstance(error, dict) else str(error)
    if response.status_code == 403 and "has not been used" in (message or ""):
        return "the Gmail API is not enabled in your Google Cloud project (APIs & Services › Library › Gmail API › Enable)"
    if response.status_code == 403 and "Mail service not enabled" in (message or ""):
        return "Gmail is not enabled for this account"
    return message or f"HTTP {response.status_code}"


def _request(account: dict[str, Any], method: str, url: str, **kwargs: Any) -> requests.Response:
    """One authorised call, renewing the access token once if Google says it has expired."""
    for attempt in range(2):
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {access_token(account, fresh=attempt > 0)}"}
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code != 401:
            return response
        kwargs["headers"] = {key: value for key, value in headers.items() if key != "Authorization"}
    return response


# ------------------------------------------------------------------ sending
def send(account: dict[str, Any], message: EmailMessage) -> str:
    """Send one message from ``account``; returns Gmail's message id."""
    address = account.get("address") or ""
    raw = message.as_bytes(policy=email.policy.SMTP)
    try:
        response = _request(account, "POST", UPLOAD, data=raw, headers={"Content-Type": "message/rfc822"}, timeout=(15, 180))
    except requests.Timeout as exc:
        # The message may have left before the answer was lost: never try another account automatically.
        raise mailer.Uncertain(f"{address}: Google did not confirm in time. Look in your Sent folder before approving it again.") from exc
    except requests.RequestException as exc:
        raise GoogleError(f"{address}: could not reach Google ({type(exc).__name__}).") from exc
    if response.status_code != 200:
        raise GoogleError(f"{address}: Gmail refused the message — {_api_error(response)}.")
    return str(response.json().get("id") or "")


def check(account: dict[str, Any]) -> str:
    """Renew access without sending anything; returns a sentence for the interface."""
    access_token(account, fresh=True)
    return "Connected with Google — " + ("sending and reading replies." if can_read(account) else "sending only.")


# ------------------------------------------------------------------ reading
class ApiMailbox:
    """The ``inbox.Mailbox`` interface over the Gmail API (Gmail search syntax, read-only)."""

    gmail = True

    def __init__(self, account: dict[str, Any]) -> None:
        if not can_read(account):
            raise GoogleError(f"{account.get('address')}: connected for sending only — reconnect with “Also read replies” ticked.")
        self.account = account
        self.address = account["address"]
        access_token(account)

    def search(self, gmail_query: str, standard: list[str], limit: int = 500) -> list[bytes]:
        ids: list[bytes] = []
        page = None
        while len(ids) < limit:
            params = {"q": gmail_query, "maxResults": 100, **({"pageToken": page} if page else {})}
            try:
                response = _request(self.account, "GET", f"{API}/messages", params=params, timeout=30)
            except requests.RequestException as exc:
                raise GoogleError(f"{self.address}: could not reach Google ({type(exc).__name__}).") from exc
            if response.status_code != 200:
                raise GoogleError(f"{self.address}: Gmail search failed — {_api_error(response)}.")
            data = response.json()
            ids.extend(item["id"].encode("ascii") for item in data.get("messages") or [])
            page = data.get("nextPageToken")
            if not page:
                break
        return ids[:limit]

    def fetch(self, uid: bytes) -> Optional[email.message.Message]:
        try:
            response = _request(self.account, "GET", f"{API}/messages/{uid.decode('ascii')}", params={"format": "raw"}, timeout=60)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        raw = response.json().get("raw") or ""
        return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=email.policy.default)

    def close(self) -> None:
        return None
