"""Fetch short plaintext excerpts from article URLs for assistant prompts."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _is_safe_http_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def extract_text_from_html(html: bytes, max_chars: int) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main")
    if root is None:
        # common content containers
        for sel in ("div.article", "div.post", "div.content", "div#content"):
            hit = soup.select_one(sel)
            if hit:
                root = hit
                break
    if root is None:
        root = soup.body or soup
    text = root.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # drop boilerplate lines that are usually nav noise
    filtered: list[str] = []
    for ln in lines:
        if len(ln) < 2:
            continue
        if re.match(r"^(cookie|subscribe|登录|注册)", ln, re.I):
            continue
        filtered.append(ln)
    out = "\n".join(filtered)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return out.strip()


def fetch_article_excerpt(url: str, *, timeout_sec: float, max_chars: int) -> str:
    """GET html and return a short plaintext excerpt. Empty on failure/paywall/no-html."""
    if not url or not _is_safe_http_url(url):
        return ""
    try:
        with httpx.Client(
            timeout=timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": _DEFAULT_UA, "Accept-Language": "zh-CN,en;q=0.9"},
        ) as client:
            r = client.get(url.strip())
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            if "html" not in ctype and "text/plain" not in ctype:
                return ""
            return extract_text_from_html(r.content, max_chars)
    except Exception:
        return ""
