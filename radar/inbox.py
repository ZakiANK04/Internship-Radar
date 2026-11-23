"""Reply and bounce detection in your own mailbox (read-only, matched messages only).

The Radar signs in over IMAP with the app password already stored for sending and
asks the mail server for exactly three kinds of messages received after you wrote:

* messages **from an address you e-mailed** through the Radar or your Apps Script;
* **delivery failures** (mailer-daemon / postmaster) that name one of those addresses;
* replies in **your application threads** (same subject line) sent from another address.

Nothing else is listed or opened, nothing is marked as read, and only a short excerpt
of each matched reply is stored locally so you can see it in the app.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
from datetime import date, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable, Optional

from . import db, google_mail, mailer
from .text import clean, fold

Log = Callable[..., None]
IMAP_HOSTS = {"smtp.gmail.com": "imap.gmail.com"}
MAX_BODY = 3_000_000
SUBJECT_MARKERS = ("ENP Alger", "ENP Algiers")
AUTO_SUBJECT = re.compile(r"^(automatic reply|auto(?:matic)?[- ]?reply|out of (?:the )?office|r[ée]ponse automatique|absence|"
                          r"abwesen|risposta automatica|respuesta autom|automatisch|autoreply)", re.I)
DECLINE = re.compile(r"unfortunately|regret|no (?:open )?(?:position|opening|internship|vacanc|funding|capacity)|not (?:able|in a position)|"
                     r"cannot (?:host|offer|take|accept)|can't (?:host|offer|take)|malheureusement|pas de (?:possibilit|place|stage|poste|financement)|"
                     r"ne (?:peux|pouvons|suis|sommes) pas|aucun(?:e)? (?:stage|possibilit|place)|d[ée]sol[ée]|purtroppo|leider|"
                     r"already (?:full|filled)|complet|not looking for|do not have", re.I)
POSITIVE = re.compile(r"interview|entretien|visio|call\b|zoom|teams|meet\b|discut|discuss|[ée]changer|available|disponib|"
                      r"send (?:me|us)|envoy(?:er|ez)|could you|pourriez|interested|int[ée]ress|welcome|bienvenu|subject|sujet|when can", re.I)
QUOTE_START = re.compile(r"^(?:on .{6,90} wrote:|le .{6,90} a .crit\s*:|(?:on|le|il giorno|am)\s.{4,110}\b20\d\d\b.{0,60}\d{1,2}\s?[:h]\s?\d{2}.*|il giorno .{6,90} ha scritto:|am .{6,90} schrieb.*:|"
                         r"-{2,}\s*(?:original message|message d'origine|forwarded).*|from:\s.+|de\s?:\s.+|_{10,})$", re.I)


# ------------------------------------------------------------------ parsing
def reply_text(message: email.message.Message, limit: int = 700) -> str:
    """The new text of a reply: plain part, without the quoted original."""
    part = message.get_body(preferencelist=("plain", "html")) if hasattr(message, "get_body") else None
    try:
        text = part.get_content() if part is not None else ""
    except (LookupError, UnicodeError, KeyError):
        text = ""
    if part is not None and part.get_content_type() == "text/html":
        from .text import html_to_text

        text = html_to_text(text)
    lines = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or QUOTE_START.match(stripped):
            break
        lines.append(stripped)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:limit]


def is_auto_reply(message: email.message.Message) -> bool:
    auto = (message.get("Auto-Submitted") or "").lower()
    if auto and auto != "no":
        return True
    if message.get("X-Autoreply") or message.get("X-Autorespond") or "auto_reply" in (message.get("Precedence") or "").lower():
        return True
    return bool(AUTO_SUBJECT.match(clean(message.get("Subject"))))


REDIRECT = re.compile(r"application instructions|apply (?:via|through|on|using)|see (?:the|my|our) .{0,40}(?:website|page)|"
                      r"postuler (?:via|sur)|candidater (?:via|sur)|formulaire|official (?:call|portal)|portail", re.I)
UNFUNDED = re.compile(r"(?:glad|happy|pleased|volontiers|plaisir|welcome).{0,160}(?:funding|financement|expenses|frais|bourse|grant)|"
                      r"(?:funding|financement|bourse).{0,120}(?:glad|happy|volontiers|own funding|propre financement|self[- ]funded)", re.I | re.S)


def tone(text: str) -> str:
    """A hint only — the reply itself is always shown: positive, unfunded, redirect, declined or neutral."""
    if UNFUNDED.search(text):
        return "unfunded"  # would host, but cannot pay
    if REDIRECT.search(text):
        return "redirect"  # asks you to apply another way
    if DECLINE.search(text):
        return "declined"
    if POSITIVE.search(text):
        return "positive"
    return "neutral"


def failed_recipients(message: email.message.Message, known: set[str]) -> set[str]:
    """Addresses a delivery-failure notice says could not be reached."""
    found = {address.strip().lower() for address in (message.get("X-Failed-Recipients") or "").split(",") if address.strip()}
    try:
        raw = message.as_string()
    except Exception:
        raw = ""
    found |= {match.lower() for match in re.findall(r"(?:Final|Original)-Recipient:\s*rfc822;\s*([^\s<>]+@[^\s<>]+)", raw, re.I)}
    if not found:
        lowered = raw.lower()
        found = {address for address in known if address in lowered}
    return found & known


def _when(message: email.message.Message) -> str:
    try:
        return parsedate_to_datetime(message.get("Date")).date().isoformat()
    except (TypeError, ValueError):
        return date.today().isoformat()


# -------------------------------------------------------------------- IMAP
class Mailbox:
    def __init__(self, account: dict[str, Any]) -> None:
        host = IMAP_HOSTS.get(account.get("host") or "", "imap.gmail.com")
        self.address = account["address"]
        self.gmail = host == "imap.gmail.com"
        self.connection = imaplib.IMAP4_SSL(host, 993, timeout=30)
        self.connection.login(account["address"], mailer.reveal(account["secret"]))
        folder = "INBOX"
        if self.gmail:  # "All Mail" also covers archived replies; its name depends on the mailbox language
            status, boxes = self.connection.list()
            for box in boxes or []:
                text = box.decode("utf-8", "replace")
                if "\\All" in text:
                    folder = text.split(' "/" ')[-1].strip('"')
                    break
        self.connection.select(f'"{folder}"', readonly=True)  # read-only: nothing is marked as read

    def search(self, gmail_query: str, standard: list[str]) -> list[bytes]:
        try:
            if self.gmail:
                self.connection.literal = gmail_query.encode("utf-8")
                status, data = self.connection.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
            else:
                status, data = self.connection.uid("SEARCH", None, *standard)
        except imaplib.IMAP4.error:
            return []
        return data[0].split() if status == "OK" and data and data[0] else []

    def fetch(self, uid: bytes) -> Optional[email.message.Message]:
        status, size = self.connection.uid("FETCH", uid, "(RFC822.SIZE)")
        big = status == "OK" and size and size[0] and int(re.search(rb"RFC822\.SIZE (\d+)", size[0]).group(1)) > MAX_BODY
        status, data = self.connection.uid("FETCH", uid, "(BODY.PEEK[HEADER])" if big else "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1], policy=email.policy.default)

    def close(self) -> None:
        try:
            self.connection.close()
            self.connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


# ------------------------------------------------------------------ records
def _awaiting() -> dict[str, dict[str, Any]]:
    """Latest unanswered e-mail per recipient address."""
    rows = db.rows(
        """SELECT a.id, a.contact_id, a.offer_id, lower(a.recipient) AS recipient, a.sent_at, a.stage, c.name AS contact_name
           FROM applications a LEFT JOIN contacts c ON c.id = a.contact_id
           WHERE a.sent_at IS NOT NULL AND a.recipient != '' AND a.stage IN ('sent', 'follow_up')
           ORDER BY a.sent_at""")
    return {row["recipient"]: row for row in rows}


def _drop_pending_follow_ups(application_id: int) -> None:
    db.execute("DELETE FROM applications WHERE sent_at IS NULL AND stage IN ('to_review', 'ready', 'scheduled') AND notes LIKE ?",
               (f"%Follow-up to application #{application_id} %",))


def record_reply(application: dict[str, Any], *, sender: str, subject: str, text: str, when: str, auto: bool) -> str:
    if auto:
        db.execute("UPDATE applications SET reply_kind = 'auto', reply_from = ?, reply_subject = ?, reply_snippet = ?, replied_at = ? "
                   "WHERE id = ? AND reply_kind IS NULL", (sender, subject[:200], text, when, application["id"]))
        return "auto"
    db.execute("UPDATE applications SET stage = 'replied', reply_kind = 'reply', reply_tone = ?, reply_from = ?, reply_subject = ?, "
               "reply_snippet = ?, replied_at = ?, updated_at = ? WHERE id = ?",
               (tone(text), sender, subject[:200], text, when, db.now(), application["id"]))
    if application.get("contact_id"):
        db.execute("UPDATE contacts SET status = 'replied' WHERE id = ?", (application["contact_id"],))
        # Every other open thread with the same person stops too.
        db.execute("UPDATE applications SET stage = 'closed' WHERE contact_id = ? AND id != ? AND stage IN ('sent', 'follow_up')",
                   (application["contact_id"], application["id"]))
    _drop_pending_follow_ups(application["id"])
    return "reply"


def record_bounce(application: dict[str, Any], when: str) -> None:
    db.execute("UPDATE applications SET stage = 'bounced', reply_kind = 'bounce', replied_at = ?, "
               "reply_snippet = 'The mail server reported that this address could not be reached.', updated_at = ? WHERE id = ?",
               (when, db.now(), application["id"]))
    if application.get("contact_id"):
        db.execute("UPDATE contacts SET email_verified = 0, status = 'new', "
                   "notes = coalesce(notes, '') || ? WHERE id = ?",
                   (f"\n[Address {application['recipient']} bounced on {when} — find the right one before writing again]", application["contact_id"]))
    _drop_pending_follow_ups(application["id"])


# -------------------------------------------------------------------- check
def check(log: Log = print) -> dict[str, int]:
    """Look for replies and bounces to the e-mails still waiting for an answer."""
    summary = {"replies": 0, "auto": 0, "bounces": 0, "accounts": 0}
    if not db.get_setting("inbox_enabled", True):
        return summary
    waiting = _awaiting()
    if not waiting:
        db.set_setting("inbox_checked_at", db.now())
        return summary
    earliest = min(row["sent_at"] for row in waiting.values())
    since = max(date.fromisoformat(earliest), date.today() - timedelta(days=150))
    gmail_after, imap_since = since.strftime("%Y/%m/%d"), since.strftime("%d-%b-%Y")
    by_surname: dict[str, list[dict[str, Any]]] = {}
    for row in waiting.values():
        parts = re.findall(r"[a-z]+", fold(row.get("contact_name") or ""))
        if parts and len(parts[-1]) >= 4:
            by_surname.setdefault(parts[-1], []).append(row)
    own = {account["address"].lower() for account in mailer.settings()["accounts"] if account.get("address")}
    errors = []
    for account in mailer.settings()["accounts"]:
        if not account.get("address"):
            continue
        if google_mail.connected(account):
            if not google_mail.can_read(account):
                continue  # connected for sending only
            opener = google_mail.ApiMailbox
        elif account.get("secret"):
            opener = Mailbox
        else:
            continue
        try:
            box = opener(account)
        except (imaplib.IMAP4.error, OSError, mailer.MailError) as exc:
            errors.append(f"{account['address']}: {_plain_error(exc)}")
            continue
        summary["accounts"] += 1
        try:
            addresses = sorted(waiting)
            seen: set[bytes] = set()
            # 1. Messages from the people you wrote to.
            for start in range(0, len(addresses), 25):
                chunk = addresses[start:start + 25]
                if box.gmail:
                    uids = box.search(f"from:({' OR '.join(chunk)}) after:{gmail_after}", [])
                else:  # standard IMAP: one search per address
                    uids = [uid for address in chunk for uid in box.search("", ["SINCE", imap_since, "FROM", address])]
                for uid in uids:
                    if uid in seen:
                        continue
                    seen.add(uid)
                    message = box.fetch(uid)
                    if message is None:
                        continue
                    sender = parseaddr(message.get("From") or "")[1].lower()
                    target = waiting.get(sender)
                    when = _when(message)
                    if target is None or when < target["sent_at"]:
                        continue
                    kind = record_reply(target, sender=sender, subject=clean(message.get("Subject")), text=reply_text(message),
                                        when=when, auto=is_auto_reply(message))
                    summary["replies" if kind == "reply" else "auto"] += 1
                    if kind == "reply":
                        waiting.pop(sender, None)
            # 2. Delivery failures naming one of those addresses.
            for uid in box.search(f"from:(mailer-daemon OR postmaster) after:{gmail_after}", ["SINCE", imap_since, "FROM", "mailer-daemon"]):
                message = box.fetch(uid)
                if message is None:
                    continue
                for address in failed_recipients(message, set(waiting)):
                    target = waiting.pop(address, None)
                    if target and _when(message) >= target["sent_at"]:
                        record_bounce(target, _when(message))
                        summary["bounces"] += 1
            # 3. Replies in your application threads sent from a different address (matched by the person's surname).
            markers = " OR ".join(f'"{marker}"' for marker in SUBJECT_MARKERS)
            for uid in box.search(f"subject:({markers}) -from:me after:{gmail_after}", ["SINCE", imap_since, "SUBJECT", SUBJECT_MARKERS[0]]):
                if uid in seen:
                    continue
                message = box.fetch(uid)
                if message is None:
                    continue
                display, sender = parseaddr(message.get("From") or "")
                sender = sender.lower()
                if sender in own or "mailer-daemon" in sender or "postmaster" in sender:
                    continue
                words = set(re.findall(r"[a-z]+", fold(f"{display} {sender.split('@')[0]}")))
                matches = [row for surname, rows in by_surname.items() if surname in words for row in rows if row["recipient"] in waiting]
                if len(matches) != 1 or _when(message) < matches[0]["sent_at"]:
                    continue
                kind = record_reply(matches[0], sender=sender, subject=clean(message.get("Subject")), text=reply_text(message),
                                    when=_when(message), auto=is_auto_reply(message))
                summary["replies" if kind == "reply" else "auto"] += 1
                if kind == "reply":
                    waiting.pop(matches[0]["recipient"], None)
        finally:
            box.close()
    db.set_setting("inbox_checked_at", db.now())
    db.set_setting("inbox_error", " | ".join(errors)[:400] if errors and not summary["accounts"] else "")
    if summary["accounts"]:
        log(f"Mailbox checked: {summary['replies']} new repl{'y' if summary['replies'] == 1 else 'ies'}, "
            f"{summary['bounces']} bounce(s), {summary['auto']} automatic repl{'y' if summary['auto'] == 1 else 'ies'}.")
    elif errors:
        log(f"Mailbox could not be read: {errors[0]}", "warn")
    return summary


def _plain_error(exc: Exception) -> str:
    """One readable sentence for the usual reasons a mailbox cannot be opened."""
    text = str(exc)
    low = text.lower()
    if any(mark in low for mark in ("getaddrinfo", "10060", "10051", "10065", "timed out", "unreachable", "not known")):
        return "no internet connection at that moment — it will be tried again automatically"
    if any(mark in low for mark in ("authenticationfailed", "invalid credentials", "application-specific password")):
        return "Gmail refused the app password — create a new one and paste it in Settings › E-mail sending"
    if "imap" in low and "disabled" in low:
        return "IMAP is switched off for this account — enable it in Gmail settings › Forwarding and POP/IMAP"
    return text[:160]


def due(minutes: int = 30) -> bool:
    last = db.get_setting("inbox_checked_at")
    if not last:
        return True
    return datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp() < datetime.now().timestamp() - minutes * 60
