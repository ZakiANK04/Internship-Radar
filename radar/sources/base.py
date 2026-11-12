"""Shared types for source connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class RawOffer:
    """One posting as published by its source, before scoring."""

    uid: str
    title: str
    org: str
    url: str
    location: str = ""
    country_hint: Optional[str] = None
    description: str = ""
    posted_at: Optional[str] = None
    deadline: Optional[str] = None
    remote: bool = False
    trusted_internship: bool = False
    contacts: list[dict] = field(default_factory=list)


@dataclass
class Source:
    """A runnable connector.  ``fetch`` returns postings already limited to plausible internships."""

    name: str
    kind: str  # company | research | board | programme
    label: str
    fetch: Callable[["Context"], list[RawOffer]]
    group: str = ""


@dataclass
class Context:
    """Run-wide filters handed to every connector."""

    allowed_countries: frozenset[str]
    detail_cap: int = 25

    def location_ok(self, location: str, hint: Optional[str] = None) -> bool:
        """True when a posting may be in a target country (unknown locations are kept)."""
        from ..text import detect_countries, normalise_country

        code = normalise_country(hint)
        if code:
            return code in self.allowed_countries
        found = detect_countries(location)
        if not found:
            return True
        return any(code in self.allowed_countries for code in found)
