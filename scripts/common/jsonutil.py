"""Parse JSON out of an LLM completion that may be wrapped in ``` fences."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_OPEN = re.compile(r"^```[a-z]*\n?", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\n?```$")


def strip_code_fences(raw: str) -> str:
    """Remove a leading ```lang fence and trailing ``` fence, if present."""
    s = (raw or "").strip()
    s = _FENCE_OPEN.sub("", s)
    s = _FENCE_CLOSE.sub("", s)
    return s.strip()


def parse_llm_json(raw: str) -> Any:
    """Strip markdown code fences then json.loads the remainder.

    Raises json.JSONDecodeError on malformed content — callers wrap this in
    their existing try/except so a bad response falls back gracefully.
    """
    return json.loads(strip_code_fences(raw))
