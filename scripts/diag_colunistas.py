"""Diagnóstico temporário: testa feeds do Martin Wolf (não grava nada)."""
import urllib.request
from urllib.parse import quote_plus

import feedparser

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
for nome, url in {
    "FT martin-wolf rss": "https://www.ft.com/martin-wolf?format=rss",
    "FT stream martin-wolf rss": "https://www.ft.com/stream/authorsId/Q0ItMDAwMDkwNw==-QXV0aG9ycw==?format=rss",
    "GN Martin Wolf": "https://news.google.com/rss/search?q=" + quote_plus('"Martin Wolf" when:14d') + "&hl=en-US&gl=US&ceid=US:en",
}.items():
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20)
        d = feedparser.parse(r.read())
        print(f"\n=== {nome}: HTTP {r.status}, {len(d.entries)} itens, titulo do feed: {d.feed.get('title')}")
        for e in d.entries[:12]:
            print(f"  - {e.get('published', '')[:16]} | autor={e.get('author', '')} | {e.get('source', {}).get('title', '')} | {e.get('title', '')[:100]} | {e.get('link', '')[:80]}")
    except Exception as e:  # noqa: BLE001
        print(f"\n=== {nome}: ERRO {e}")
