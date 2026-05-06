from __future__ import annotations

import re

_CN_YAHOO = re.compile(r"^\d{6}\.(SS|SZ)$")


def is_valid_cn_yahoo_symbol(symbol: str) -> bool:
    s = symbol.strip().upper()
    return bool(_CN_YAHOO.match(s))
