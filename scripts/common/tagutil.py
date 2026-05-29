"""Whitespace-normalize and de-duplicate a tag list (case-insensitive)."""
from __future__ import annotations

import re
from typing import Iterable, Optional


def normalize_tags(values: Optional[Iterable[str]]) -> list[str]:
    """Collapse internal whitespace, drop empties, de-dup case-insensitively.

    Order-preserving. Matches the behavior that was duplicated as normalize_tags
    in cafc_daily.py and ptab_daily.py.
    """
    seen: set[str] = set()
    tags: list[str] = []
    for value in values or []:
        tag = re.sub(r"\s+", " ", str(value).strip())
        if not tag:
            continue
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags
