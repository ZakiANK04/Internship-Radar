"""Source registry: every live connector the Scout agent can run."""

from __future__ import annotations

import json

from .. import config
from . import ats, boards, research
from .base import Context, RawOffer, Source

__all__ = ["Context", "RawOffer", "Source", "load_sources"]


def load_sources() -> list[Source]:
    sources: list[Source] = [*research.all_sources(), *boards.all_sources()]
    registry = json.loads((config.SEED_DIR / "boards.json").read_text(encoding="utf-8"))
    for entry in registry:
        kind, token, name = entry["ats"], entry["token"], entry["name"]
        if kind == "greenhouse":
            sources.append(ats.greenhouse(token, name))
        elif kind == "lever":
            sources.append(ats.lever(token, name))
        elif kind == "lever_eu":
            sources.append(ats.lever(token, name, host="api.eu.lever.co"))
        elif kind == "ashby":
            sources.append(ats.ashby(token, name))
        elif kind == "smartrecruiters":
            sources.append(ats.smartrecruiters(token, name))
        elif kind == "workday":
            sources.append(ats.workday(token, entry["pod"], entry["site"], name, mena=bool(entry.get("mena"))))
    return sources
