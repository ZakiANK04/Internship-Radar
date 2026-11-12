"""Polite HTTP client: one shared session, per-host spacing, bounded retries.

Every connector goes through :func:`get` / :func:`post`.  A host that answers
429 or 403 is put on cooldown for the rest of the run instead of being retried,
so an unattended run can never turn into a request burst.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from . import config

DEFAULT_INTERVAL = 0.7
HOST_INTERVALS = {
    "boards-api.greenhouse.io": 0.25,
    "api.lever.co": 0.25,
    "api.eu.lever.co": 0.25,
    "api.ashbyhq.com": 0.25,
    "api.smartrecruiters.com": 0.3,
    "api.openalex.org": 0.5,
    "www.hellowork.com": 1.5,
    "arxiv.org": 3.0,
    "generativelanguage.googleapis.com": 4.2,
}


class FetchError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class _HostGate:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.blocked_until = 0.0


_gates: dict[str, _HostGate] = {}
_gates_lock = threading.Lock()
_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "en,fr;q=0.8"})
        _local.session = session
    return session


def _gate(host: str) -> _HostGate:
    with _gates_lock:
        return _gates.setdefault(host, _HostGate())


def _wait(host: str) -> None:
    gate = _gate(host)
    interval = HOST_INTERVALS.get(host, DEFAULT_INTERVAL)
    with gate.lock:
        now = time.monotonic()
        if gate.blocked_until > now:
            raise FetchError(f"{host} is cooling down after a rate-limit/refusal", 429)
        delay = gate.next_at - now
        gate.next_at = max(now, gate.next_at) + interval
    if delay > 0:
        time.sleep(delay)


def is_cooling_down(host: str) -> bool:
    """True when ``host`` refused or rate-limited this client earlier in the run."""
    return _gate(host).blocked_until > time.monotonic()


def request(method: str, url: str, *, timeout: float = 25, retries: int = 1, **kwargs: Any) -> requests.Response:
    host = urlparse(url).netloc
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        _wait(host)
        try:
            response = _session().request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code == 429:
            _gate(host).blocked_until = time.monotonic() + 600
            raise FetchError(f"{host} rate limited this client (429)", 429)
        if response.status_code >= 500 and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            last = FetchError(f"{response.status_code} from {host}", response.status_code)
            continue
        if response.status_code >= 400:
            detail = ""
            if "json" in response.headers.get("content-type", ""):
                try:
                    detail = str((response.json().get("error") or {}).get("message") or "")[:200]
                except (ValueError, AttributeError):
                    detail = ""
            raise FetchError(detail or f"HTTP {response.status_code} for {url.split('?')[0]}", response.status_code)
        return response
    raise FetchError(f"Request failed for {url}: {last}")


def get(url: str, **kwargs: Any) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request("POST", url, **kwargs)


def get_json(url: str, **kwargs: Any) -> Any:
    response = get(url, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise FetchError(f"Invalid JSON from {url}") from exc


def post_json(url: str, payload: Any, **kwargs: Any) -> Any:
    headers = {"Content-Type": "application/json", "Accept": "application/json", **kwargs.pop("headers", {})}
    response = post(url, json=payload, headers=headers, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise FetchError(f"Invalid JSON from {url}") from exc


def probe(url: str, timeout: float = 15) -> tuple[str, Optional[int]]:
    """Check that a page is reachable.  Returns (status, http_code).

    ``blocked`` means the site refused an automated client (it usually opens
    fine in a browser); ``dead`` means the page no longer exists.
    """
    try:
        _wait(urlparse(url).netloc)
        response = _session().get(url, timeout=timeout, allow_redirects=True, stream=True)
        code = response.status_code
        response.close()
    except FetchError:
        return "blocked", None
    except requests.RequestException:
        return "unreachable", None
    if code in (202, 401, 403, 405, 406, 429, 999):
        return "blocked", code
    if code < 400:
        return "ok", code
    if code in (404, 410):
        return "dead", code
    return "unreachable", code
