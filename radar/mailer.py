"""Sending applications from the candidate's own mailbox.

Only applications the candidate has approved are ever sent, each one once, after
the configured delay.  The primary account is tried first; if it refuses, the
fallback account is used.  App passwords are encrypted with Windows DPAPI, so
the stored value can only be read by the same Windows user on this machine.
"""

from __future__ import annotations

import base64
import ctypes
import re
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Optional

from . import config, db

try:
    from .local_profile import DEFAULT_MAIL
except ImportError:
    # Do not ship personal sender addresses or credentials in the public source.
    DEFAULT_MAIL: dict[str, Any] = {
        "accounts": [
            {"address": "", "host": "smtp.gmail.com", "port": 465, "security": "ssl", "secret": ""},
            {"address": "", "host": "smtp.gmail.com", "port": 465, "security": "ssl", "secret": ""},
        ],
        "delay_hours": 6, "daily_cap": 15, "attach_letter": True,
    }
_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")


class MailError(RuntimeError):
    pass


# ------------------------------------------------------------------ secrets
class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise MailError("Password storage relies on Windows; this machine is not running Windows.")
    source = _Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    call = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise MailError("Windows could not " + ("protect" if protect else "read") + " the stored password.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def protect(secret: str) -> str:
    return base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii")


def reveal(stored: str) -> str:
    return _dpapi(base64.b64decode(stored), False).decode("utf-8") if stored else ""


# ----------------------------------------------------------------- settings
def settings() -> dict[str, Any]:
    stored = db.get_setting("mail") or {}
    merged = {**DEFAULT_MAIL, **stored}
    saved = stored.get("accounts") or []
    merged["accounts"] = [{**default, **(saved[index] if index < len(saved) else {})}
                          for index, default in enumerate(DEFAULT_MAIL["accounts"])]
    return merged


def public_settings() -> dict[str, Any]:
    """Settings for the interface: never includes a password, only whether one is stored."""
    current = settings()
    return {
        "accounts": [{"address": account["address"], "host": account["host"], "port": account["port"],
                      "security": account["security"], "has_password": bool(account.get("secret"))}
                     for account in current["accounts"]],
        "delay_hours": current["delay_hours"], "daily_cap": current["daily_cap"], "attach_letter": current["attach_letter"],
        "ready": any(account.get("secret") and account.get("address") for account in current["accounts"]),
        "sent_today": sent_today(),
    }


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    current = settings()
    for index, incoming in enumerate((values.get("accounts") or [])[:2]):
        account = current["accounts"][index]
        address = str(incoming.get("address") or "").strip()
        if address and not _ADDRESS.match(address):
            raise MailError(f"“{address}” is not a valid e-mail address.")
        account["address"] = address
        for key in ("host", "security"):
            if incoming.get(key):
                account[key] = str(incoming[key]).strip()
        if incoming.get("port"):
            account["port"] = int(incoming["port"])
        password = str(incoming.get("password") or "").replace(" ", "")  # Google shows app passwords in groups of four
        if password:
            account["secret"] = protect(password)
        if incoming.get("clear_password"):
            account["secret"] = ""
    if "delay_hours" in values:
        current["delay_hours"] = max(0.0, min(72.0, float(values["delay_hours"])))
    if "daily_cap" in values:
        current["daily_cap"] = max(1, min(60, int(values["daily_cap"])))
    if "attach_letter" in values:
        current["attach_letter"] = bool(values["attach_letter"])
    db.set_setting("mail", current)
    return public_settings()


# ------------------------------------------------------------- attachments
def _file_slug(text: str) -> str:
    import unicodedata
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", plain).strip("_")[:40] or "application"


def cv_path(language: str) -> Optional[Path]:
    name = (db.profile().get("cv_files") or {}).get((language or "EN").upper())
    path = config.ROOT_DIR / name if name else None
    return path if path and path.exists() else None


def letter_pdf(application: dict[str, Any]) -> bytes:
    """Lay the cover letter out on one A4 page (two if the text is long)."""
    from fpdf import FPDF

    profile = db.profile()
    french = (application.get("language") or "EN").upper() == "FR"
    pdf = FPDF(format="A4")
    pdf.set_margins(22, 20, 22)
    pdf.set_auto_page_break(True, margin=18)
    fonts = Path("C:/Windows/Fonts")
    if (fonts / "calibri.ttf").exists():
        pdf.add_font("Body", "", str(fonts / "calibri.ttf"))
        pdf.add_font("Body", "B", str(fonts / "calibrib.ttf"))
        family = "Body"
    else:
        family = "Helvetica"
    pdf.add_page()

    def write(text: str, size: float = 11, style: str = "", gap: float = 0, colour: int = 30) -> None:
        if family == "Helvetica":
            text = text.encode("latin-1", "replace").decode("latin-1")
        pdf.set_font(family, style, size)
        pdf.set_text_color(colour)
        pdf.multi_cell(0, size * 0.48, text, new_x="LMARGIN", new_y="NEXT")
        if gap:
            pdf.ln(gap)

    write(profile["full_name"], 17, "B", 1)
    write(f"{profile['city']}  ·  {profile['phone']}  ·  {profile['email']}", 9.5, colour=95)
    write("  ·  ".join(link.replace("https://", "") for link in (profile.get("linkedin"), profile.get("github")) if link), 9.5, gap=2, colour=95)
    pdf.set_draw_color(190)
    pdf.line(22, pdf.get_y(), 188, pdf.get_y())
    pdf.ln(6)
    today = date.today()
    months_fr = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    stamp = f"Alger, le {today.day} {months_fr[today.month - 1]} {today.year}" if french else f"Algiers, {today.strftime('%d %B %Y').lstrip('0')}"
    write(stamp, 10.5, gap=3, colour=95)
    recipient = application.get("offer_org") or application.get("contact_org")
    if application.get("contact_name"):
        write(application["contact_name"], 11, "B")
    if recipient:
        write(recipient, 11, "" if application.get("contact_name") else "B")
    pdf.ln(3)
    write(("Objet : " if french else "Subject: ") + (application.get("subject") or ""), 11, "B", 4)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", application.get("letter") or application.get("body") or "") if part.strip()]
    for paragraph in paragraphs:
        # The e-mail style signature block is replaced by a plain signature at the end of the letter.
        if profile["email"] in paragraph and profile["phone"] in paragraph:
            closing = paragraph.split(profile["full_name"])[0].strip()
            if closing:
                write(closing, 11, gap=1)
            continue
        write(paragraph, 11, gap=3)
    pdf.ln(2)
    write(profile["full_name"], 11, "B")
    return bytes(pdf.output())


def letter_filename(application: dict[str, Any]) -> str:
    french = (application.get("language") or "EN").upper() == "FR"
    target = _file_slug(application.get("offer_org") or application.get("contact_org") or "")
    return f"{'Lettre_de_motivation' if french else 'Cover_Letter'}_candidate_{target}.pdf"


def load_application(application_id: int) -> Optional[dict[str, Any]]:
    return db.one(
        """SELECT a.*, o.title AS offer_title, o.org AS offer_org, c.name AS contact_name, c.org AS contact_org
           FROM applications a LEFT JOIN offers o ON o.id = a.offer_id LEFT JOIN contacts c ON c.id = a.contact_id
           WHERE a.id = ?""", (application_id,))


def build_message(application: dict[str, Any], sender: Optional[str] = None, draft: bool = False) -> EmailMessage:
    """The exact e-mail that is sent: short message, cover letter PDF, CV in the posting's language."""
    profile = db.profile()
    current = settings()
    address = sender or next((account["address"] for account in current["accounts"] if account.get("address")), profile["email"])
    message = EmailMessage()
    message["Subject"] = application.get("subject") or ""
    message["From"] = f"{profile['full_name']} <{address}>"
    if application.get("recipient"):
        message["To"] = application["recipient"]
    if draft:
        message["X-Unsent"] = "1"
    else:
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=address.split("@")[-1])
    message.set_content(application.get("body") or "")
    if current["attach_letter"] and (application.get("letter") or "").strip():
        message.add_attachment(letter_pdf(application), maintype="application", subtype="pdf", filename=letter_filename(application))
    path = cv_path(application.get("language") or "EN")
    if path:
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    return message


# ------------------------------------------------------------------ sending
def _close(server: Optional[smtplib.SMTP]) -> None:
    if server is not None:
        try:
            server.close()
        except OSError:
            pass


def _routes(account: dict[str, Any]) -> list[tuple[int, str]]:
    """Ports to try, in order.  Many networks block one of Gmail's two submission ports."""
    first = (int(account["port"]), account.get("security") or "starttls")
    if first[1] == "none":  # local test server: no alternative route
        return [first]
    return list(dict.fromkeys([first, (465, "ssl"), (587, "starttls")]))


def _connect(account: dict[str, Any]) -> smtplib.SMTP:
    password = reveal(account.get("secret") or "")
    if not account.get("address") or not password:
        raise MailError(f"No app password stored for {account.get('address') or 'this account'}.")
    context = ssl.create_default_context()
    failures = []
    for port, security in _routes(account):
        server: Optional[smtplib.SMTP] = None
        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(account["host"], port, timeout=12, context=context)
            else:
                server = smtplib.SMTP(account["host"], port, timeout=12)
                server.ehlo()
                if security != "none":
                    server.starttls(context=context)
                    server.ehlo()
            server.login(account["address"], password)
            return server
        except smtplib.SMTPAuthenticationError as exc:
            _close(server)
            raise MailError(f"{account['address']}: Google refused the sign-in ({exc.smtp_code}). Use a 16-character app password, "
                            "not your normal password; school accounts may need the administrator to allow app passwords.") from exc
        except (smtplib.SMTPException, OSError) as exc:
            _close(server)
            failures.append(f"port {port}: {exc}")
    raise MailError(f"{account['address']}: could not reach {account['host']} ({'; '.join(failures)}). "
                    "Your network is blocking outgoing mail — try another connection or a phone hotspot.")


def test_accounts() -> list[dict[str, Any]]:
    """Sign in to each configured account without sending anything."""
    results = []
    for account in settings()["accounts"]:
        if not account.get("address"):
            continue
        try:
            _connect(account).quit()
            results.append({"address": account["address"], "ok": True, "message": "Signed in successfully."})
        except MailError as exc:
            results.append({"address": account["address"], "ok": False, "message": str(exc)})
    return results


def sent_today() -> int:
    return int((db.one("SELECT count(*) AS n FROM applications WHERE sent_from IS NOT NULL AND sent_at = ?",
                       (date.today().isoformat(),)) or {}).get("n", 0))


def validate(application: dict[str, Any]) -> None:
    """Raise :class:`MailError` unless the application can be sent as it stands."""
    recipient = (application.get("recipient") or "").strip()
    if not recipient:
        raise MailError("No recipient address. This posting is applied to through the employer's web form — open it and apply there.")
    if not _ADDRESS.match(recipient):
        raise MailError(f"“{recipient}” is not a valid e-mail address.")
    if not (application.get("subject") or "").strip() or len((application.get("body") or "").strip()) < 40:
        raise MailError("The subject or the message is empty.")
    if not cv_path(application.get("language") or "EN"):
        raise MailError("The CV file for this language was not found in the project folder.")
    if not public_settings()["ready"]:
        raise MailError("E-mail sending is not set up yet: add an app password under Profile & priorities → E-mail sending.")


def send(application: dict[str, Any]) -> str:
    """Send one application; returns the address it left from."""
    validate(application)
    errors = []
    for account in settings()["accounts"]:
        if not account.get("address") or not account.get("secret"):
            continue
        try:
            server = _connect(account)
            try:
                server.send_message(build_message(application, sender=account["address"]))
            finally:
                try:
                    server.quit()
                except smtplib.SMTPException:
                    pass
            return account["address"]
        except MailError as exc:
            errors.append(str(exc))
        except (smtplib.SMTPException, OSError) as exc:
            errors.append(f"{account['address']}: {exc}")
    raise MailError(" | ".join(errors) or "No account is configured.")


def approve(application_id: int, delay_hours: Optional[float] = None) -> dict[str, Any]:
    """Record the candidate's validation and queue the e-mail for sending after the delay."""
    application = load_application(application_id)
    if application is None:
        raise MailError("Unknown application.")
    if application["stage"] in ("sent", "follow_up", "interview", "offer") and application.get("sent_at"):
        raise MailError("This application has already been sent.")
    validate(application)
    hours = settings()["delay_hours"] if delay_hours is None else delay_hours
    send_at = (datetime.now(timezone.utc) + timedelta(hours=float(hours))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    db.execute("UPDATE applications SET stage = 'scheduled', approved_at = ?, send_at = ?, send_error = NULL, updated_at = ? WHERE id = ?",
               (db.now(), send_at, db.now(), application_id))
    return {"send_at": send_at, "delay_hours": hours}


def _record_sent(application: dict[str, Any], sender: str) -> None:
    days = int(db.priorities().get("follow_up_days") or 10)
    today = date.today()
    db.execute("UPDATE applications SET stage = 'sent', sent_at = ?, follow_up_at = ?, sent_from = ?, send_error = NULL, updated_at = ? WHERE id = ?",
               (today.isoformat(), (today + timedelta(days=days)).isoformat(), sender, db.now(), application["id"]))
    if application.get("offer_id"):
        db.execute("UPDATE offers SET status = 'applied' WHERE id = ?", (application["offer_id"],))
    if application.get("contact_id"):
        db.execute("UPDATE contacts SET status = 'contacted' WHERE id = ?", (application["contact_id"],))


def deliver(application_id: int) -> dict[str, Any]:
    """Claim one scheduled application, send it, and record the outcome.  Safe against double sending."""
    with db.write() as connection:
        claimed = connection.execute("UPDATE applications SET stage = 'sending' WHERE id = ? AND stage = 'scheduled'", (application_id,)).rowcount
    if not claimed:
        return {"id": application_id, "sent": False, "error": "not scheduled"}
    application = load_application(application_id)
    try:
        sender = send(application)
    except MailError as exc:
        db.execute("UPDATE applications SET stage = 'ready', send_at = NULL, send_error = ?, updated_at = ? WHERE id = ?",
                   (str(exc)[:500], db.now(), application_id))
        return {"id": application_id, "sent": False, "error": str(exc)}
    _record_sent(application, sender)
    return {"id": application_id, "sent": True, "from": sender, "to": application["recipient"]}


def send_due(log=None) -> list[dict[str, Any]]:
    """Send every approved application whose delay has elapsed, within the daily cap."""
    due = db.rows("SELECT id FROM applications WHERE stage = 'scheduled' AND send_at IS NOT NULL AND send_at <= ? ORDER BY send_at", (db.now(),))
    results = []
    cap = int(settings()["daily_cap"])
    for row in due:
        if sent_today() >= cap:
            if log:
                log(f"Daily limit of {cap} e-mails reached — the remaining {len(due) - len(results)} go out tomorrow.")
            break
        outcome = deliver(row["id"])
        results.append(outcome)
        if log:
            log(f"Sent to {outcome['to']} from {outcome['from']}." if outcome["sent"] else f"Could not send application {row['id']}: {outcome['error']}",
                "info" if outcome["sent"] else "warn")
    return results
