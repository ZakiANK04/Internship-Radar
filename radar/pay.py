"""Internship pay ("gratification", salary, stipend) read from postings.

Every figure shown comes from the posting itself (``source = "stated"``) or from
a structured field the job board publishes.  When a French posting states
nothing, the legal minimum gratification is shown instead and labelled as such
(``source = "legal_min"``), never as the employer's offer.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .text import fold

# Legal minimum internship gratification in France: 15 % of the hourly social-security
# ceiling.  4.35 €/h is the published rate for 2025; update it when the rate changes.
FR_MIN_HOURLY = 4.35
FR_MIN_MONTHLY = round(FR_MIN_HOURLY * 151.67)  # full-time month (35 h/week)

# Approximate conversion rates to euros, used only to compare offers; the original amount is always shown.
TO_EUR = {"EUR": 1.0, "CHF": 1.06, "GBP": 1.17, "USD": 0.92, "AED": 0.25, "SAR": 0.245, "QAR": 0.25,
          "EGP": 0.019, "SEK": 0.088, "DKK": 0.134, "NOK": 0.086, "PLN": 0.23, "CZK": 0.04, "KWD": 3.0}
PERIOD_TO_MONTH = {"hour": 151.67, "day": 21.67, "week": 4.33, "month": 1.0, "year": 1 / 12}

_CURRENCY = r"(?P<cur>€|eur(?:os?)?|chf|£|gbp|\$|usd|aed|sar|qar|egp|le|sek|dkk|nok|pln|czk|kwd)"
_AMOUNT = r"(?P<amt>\d{1,3}(?:[ .,  ]\d{3})+|\d+(?:[.,]\d{1,2})?)\s*(?P<k>k)?"
_RANGE_TAIL = r"(?:\s*(?:-|–|à|to|bis|a|and|et)\s*(?P<amt2>\d{1,3}(?:[ .,  ]\d{3})+|\d+(?:[.,]\d{1,2})?)\s*(?P<k2>k)?)?"
_PERIOD = (r"(?P<per>(?:par|per|/|a|an|pro|al|by)?\s*(?:mois|month|monat|mese|mes|maand|heure|hour|h|stunde|ora|hr|"
           r"jour|day|tag|giorno|semaine|week|woche|an|annee|year|yr|jahr|anno|annum|mensuel(?:le)?|monthly|"
           r"brut\s*(?:mensuel|par mois)|gross per month))")
PAY_WORDS = re.compile(
    r"gratification|r[ée]mun[ée]r\w*|salaire|salary|stipend|indemnit|compensation|verg[üu]e?tung|retribuzione|"
    r"rimborso|stage pay|paid|pay\b|bezahlung|bruto|brut|gross|net\b|allowance|\bpay range|per month|par mois|/\s*mois",
    re.I)

# currency before amount, or amount before currency, each optionally followed by a period
_PATTERNS = [
    re.compile(_CURRENCY + r"\s*" + _AMOUNT + _RANGE_TAIL + r"(?:\s*(?:brut|bruts|gross|net)?\s*" + _PERIOD + r")?", re.I),
    re.compile(_AMOUNT + _RANGE_TAIL + r"\s*" + _CURRENCY + r"(?:\s*(?:brut|bruts|gross|net|mensuels?)?\s*" + _PERIOD + r")?", re.I),
]
_NOT_PAY = re.compile(r"chiffre d'affaires|revenue|turnover|lev[ée]e|raised|funding|capital|valuation|budget|clients?|"
                      r"million|milliard|billion|m€|bn|users|customers|employees|salaries\b", re.I)


# Benefits quoted per month are not pay: meal vouchers, transport, sport, equipment or tool budgets.
_PERK = re.compile(r"budget|abonnement|subscription|remboursement|reimburse|ticket|swile|mutuelle|transport|navigo|v[ée]lo|bike|"
                   r"sport|gym|t[ée]l[ée]travail|remote work|[ée]quipement|equipment|outils|tools|repas|meal|lunch|cantine|"
                   r"logement|housing|relocation|d[ée]m[ée]nagement|prime de|bonus|\bcar\b|essence|fuel", re.I)


def _number(text: Optional[str], kilo: Optional[str]) -> Optional[float]:
    if not text:
        return None
    raw = text.replace(" ", " ").replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", raw):
        value = float(re.sub(r"[ .,]", "", raw))
    else:
        value = float(raw.replace(",", "."))
    return value * 1000 if kilo else value


def _currency(token: str) -> str:
    token = token.lower()
    return {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP", "$": "USD", "le": "EGP"}.get(token, token.upper())


def _period(token: Optional[str], amount: float) -> Optional[str]:
    text = fold(token or "")
    if re.search(r"heure|hour|\bh\b|stunde|\bora\b|hr", text):
        return "hour"
    if re.search(r"jour|day|tag|giorno", text):
        return "day"
    if re.search(r"semaine|week|woche", text):
        return "week"
    if re.search(r"mois|month|monat|mese|\bmes\b|maand|mensuel", text):
        return "month"
    if re.search(r"\ban\b|annee|year|yr|jahr|anno|annum", text):
        return "year"
    # No period written: infer from the size of the figure.
    if amount < 25:
        return "hour"
    if amount <= 6000:
        return "month"
    if amount >= 9000:
        return "year"
    return None


def _monthly_eur(amount: float, period: str, currency: str) -> Optional[int]:
    rate = TO_EUR.get(currency)
    factor = PERIOD_TO_MONTH.get(period)
    if rate is None or factor is None:
        return None
    return int(round(amount * factor * rate))


def _plausible(monthly: Optional[int]) -> bool:
    # Internship pay worldwide sits roughly between a third of the French minimum and ~6k€/month.
    return monthly is not None and 150 <= monthly <= 7000


def from_text(text: str) -> Optional[dict[str, Any]]:
    """Find the stated pay in a posting.  Only figures near a pay word are trusted."""
    if not text:
        return None
    best: Optional[dict[str, Any]] = None
    for window in _pay_windows(text):
        for pattern in _PATTERNS:
            for match in pattern.finditer(window):
                context = window[max(0, match.start() - 60): match.end() + 30]
                if _NOT_PAY.search(context) and not PAY_WORDS.search(context):
                    continue
                if _PERK.search(window[max(0, match.start() - 70): match.start()]) and not re.search(
                        r"gratification|salaire|salary|stipend|r[ée]mun[ée]ration", window[max(0, match.start() - 70): match.start()], re.I):
                    continue
                low = _number(match.group("amt"), match.group("k"))
                high = _number(match.group("amt2"), match.group("k2")) or low
                if low is None:
                    continue
                currency = _currency(match.group("cur"))
                period = _period(match.groupdict().get("per"), low)
                if period is None:
                    continue
                monthly_low, monthly_high = _monthly_eur(low, period, currency), _monthly_eur(high, period, currency)
                if not _plausible(monthly_low):
                    continue
                candidate = {"min": low, "max": high if high != low else None, "currency": currency, "period": period,
                             "monthly_eur": monthly_low, "monthly_eur_max": monthly_high if high != low else None,
                             "text": re.sub(r"\s+", " ", match.group(0)).strip()[:80], "source": "stated"}
                if best is None:
                    best = candidate
        if best:
            return best
    return None


def _pay_windows(text: str) -> list[str]:
    """Text around pay words, so that unrelated figures elsewhere in the posting are ignored."""
    windows = []
    for match in PAY_WORDS.finditer(text):
        windows.append(text[max(0, match.start() - 40): match.end() + 160])
        if len(windows) >= 12:
            break
    return windows


def from_structured(value: Any) -> Optional[dict[str, Any]]:
    """Normalise a structured salary field: {'min', 'max', 'currency', 'period'|'interval'}."""
    if not isinstance(value, dict):
        return None
    low = value.get("min") or value.get("minValue")
    high = value.get("max") or value.get("maxValue") or low
    if low in (None, "", 0):
        return None
    currency = str(value.get("currency") or "EUR").upper()
    period_raw = fold(str(value.get("period") or value.get("interval") or value.get("unitText") or ""))
    period = _period(period_raw.replace("per-", "").replace("_", " "), float(low))
    if period is None:
        return None
    monthly = _monthly_eur(float(low), period, currency)
    if not _plausible(monthly):
        return None
    return {"min": float(low), "max": float(high) if high and float(high) != float(low) else None, "currency": currency,
            "period": period, "monthly_eur": monthly,
            "monthly_eur_max": _monthly_eur(float(high), period, currency) if high and float(high) != float(low) else None,
            "text": "", "source": "stated"}


def assess(text: str, country: Optional[str], structured: Any = None, hint: str = "") -> dict[str, Any]:
    """Best available pay information for one offer."""
    found = from_structured(structured) or from_text(hint) or from_text(text)
    if found:
        return found
    if country == "FR":
        return {"monthly_eur": FR_MIN_MONTHLY, "currency": "EUR", "period": "month", "source": "legal_min",
                "text": f"Not stated — French legal minimum ≈ {FR_MIN_MONTHLY} €/month ({FR_MIN_HOURLY} €/h)"}
    if re.search(r"unpaid|non r[ée]mun[ée]r|sans r[ée]mun[ée]ration|unbezahlt|non retribuit", fold(text or "")):
        return {"monthly_eur": 0, "source": "unpaid", "text": "Unpaid according to the posting"}
    return {"monthly_eur": None, "source": "unknown", "text": "Not stated"}


def label(pay: Optional[dict[str, Any]]) -> str:
    """Short display string, e.g. '1 400 €/mo' or '≈ 660 €/mo (legal min.)'."""
    if not pay or pay.get("monthly_eur") is None:
        return "Not stated"
    if pay.get("source") == "unpaid":
        return "Unpaid"
    amount = f"{pay['monthly_eur']:,}".replace(",", " ")
    if pay.get("monthly_eur_max"):
        amount += f"–{pay['monthly_eur_max']:,}".replace(",", " ")
    suffix = " (legal min.)" if pay.get("source") == "legal_min" else ""
    approx = "≈ " if pay.get("currency") not in (None, "EUR") or pay.get("period") != "month" or suffix else ""
    return f"{approx}{amount} €/mo{suffix}"


def backfill(only_missing: bool = True) -> int:
    """Compute pay for stored offers (used after an upgrade)."""
    from . import db

    query = "SELECT id, description, country, title FROM offers" + (" WHERE pay IS NULL" if only_missing else "")
    rows = db.rows(query)
    with db.write() as connection:
        for row in rows:
            info = assess(f"{row['title']}\n{row['description'] or ''}", row["country"])
            connection.execute("UPDATE offers SET pay = ? WHERE id = ?", (json.dumps(info, ensure_ascii=False), row["id"]))
    return len(rows)
