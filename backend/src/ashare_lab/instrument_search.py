"""Offline aliases for display-name lookup, never for historical ST decisions."""

import re
import unicodedata
from functools import lru_cache
from typing import Any

from pypinyin import Style, lazy_pinyin


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


@lru_cache(maxsize=20000)
def phonetics(name: str) -> tuple[str, str]:
    return (
        compact("".join(lazy_pinyin(name, style=Style.FIRST_LETTER))),
        compact("".join(lazy_pinyin(name))),
    )


def search_rank(row: dict[str, Any], query: str) -> int | None:
    query = compact(query)
    if not query:
        return 0
    code = compact(str(row["stock_code"]))
    name = compact(str(row.get("stock_name") or row.get("name") or ""))
    initials, full = phonetics(str(row.get("stock_name") or row.get("name") or ""))
    aliases = [
        code,
        code.split(".")[0],
        name,
        compact(str(row.get("cnspell") or "")),
        initials,
        full,
    ]
    if any(query == alias for alias in aliases):
        return 0
    if any(alias.startswith(query) for alias in aliases):
        return 1
    if any(query in alias for alias in aliases):
        return 2
    return None
