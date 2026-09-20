"""Public news headlines; keyword temperature is a transparent proxy only."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

from app.http import fetch

POSITIVE = ("beat", "beats", "record", "growth", "upgrade", "raise", "surge", "outperform")
NEGATIVE = ("miss", "misses", "downgrade", "downgraded", "cut", "decline", "warning", "lawsuit", "slump")


def headline_temperature(titles: list[str]) -> float | None:
    marked = []
    for title in titles:
        words = set(re.findall(r"[a-z]+", title.lower()))
        score = int(bool(words.intersection(POSITIVE))) - int(bool(words.intersection(NEGATIVE)))
        if score:
            marked.append(score)
    return sum(marked) / len(marked) if len(marked) >= 2 else None


def get_news(ticker: str, company: str | None = None) -> dict:
    query = (f'("{ticker}" OR "{company}") stock when:90d'
             if company and company.upper() != ticker else f'"{ticker}" stock when:90d')
    xml = fetch("https://news.google.com/rss/search",
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                namespace="news_rss", ttl=3600, timeout=6, retries=1)
    root = ElementTree.fromstring(xml)
    articles = []
    for item in root.findall(".//channel/item")[:12]:
        title, link = item.findtext("title"), item.findtext("link")
        if not title or not link or urlparse(link).scheme not in ("http", "https"):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
        if abs((datetime.now(timezone.utc) - published).days) > 90:
            continue
        articles.append({"title": title[:250], "url": link,
                         "published": published.date().isoformat()})
    return {"articles": articles, "temperature": headline_temperature([a["title"] for a in articles]),
            "as_of": datetime.now(timezone.utc).date().isoformat(),
            "source": "Google News RSS", "method": "English headline keywords; minimum two directional titles"}
