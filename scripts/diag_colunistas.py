"""Diagnóstico temporário: testa buscas pelos colunistas (não grava nada)."""
import urllib.request
from urllib.parse import quote_plus

import feedparser

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
EN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"
PT = "https://news.google.com/rss/search?q={}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
buscas = {
    "T1 nome": EN.format(quote_plus('"Nick Timiraos" when:7d')),
    "T2 nome+site": EN.format(quote_plus('"Nick Timiraos" site:wsj.com when:7d')),
    "T3 sobrenome": EN.format(quote_plus('Timiraos when:7d')),
    "T4 inauthor": EN.format(quote_plus('author:"Nick Timiraos" when:7d')),
    "A1 nome": PT.format(quote_plus('"Alex Ribeiro" when:7d')),
    "A2 nome+site": PT.format(quote_plus('"Alex Ribeiro" site:valor.globo.com when:7d')),
    "A3 nome+valor": PT.format(quote_plus('"Alex Ribeiro" Valor when:7d')),
}
for nome, url in buscas.items():
    d = feedparser.parse(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())
    print(f"\n=== {nome}: {len(d.entries)} itens")
    for e in d.entries[:12]:
        print(f"  - {e.get('published', '')[:16]} | {e.get('source', {}).get('title', '')} | {e.get('title', '')[:110]}")

for nome, url in {"Valor RSS": "https://valor.globo.com/rss/valor/",
                  "Valor opinião Alex (rss?)": "https://valor.globo.com/rss/opiniao/alex-ribeiro/",
                  "Valor coluna Alex (html)": "https://valor.globo.com/opiniao/alex-ribeiro/",
                  "WSJ autor (html)": "https://www.wsj.com/news/author/nick-timiraos"}.items():
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20)
        corpo = r.read()
        print(f"\n=== {nome}: HTTP {r.status}, {len(corpo)} bytes")
        if "RSS" in nome or corpo.lstrip().startswith(b"<?xml") or b"<rss" in corpo[:500]:
            d = feedparser.parse(corpo)
            autores = sorted({e.get("author", "") for e in d.entries})
            print("  autores no RSS:", autores[:30])
            for e in d.entries[:5]:
                print(f"  - {e.get('author', '')} | {e.get('title', '')[:100]} | {e.get('link', '')[:100]}")
        else:
            import re
            titulos = re.findall(rb'<a[^>]+href="([^"]+)"[^>]*>\s*([^<]{30,140})</a>', corpo)[:10]
            for h, t in titulos:
                print("  -", t.decode("utf-8", "ignore").strip()[:100], "|", h.decode()[:100])
    except Exception as e:  # noqa: BLE001
        print(f"\n=== {nome}: ERRO {e}")
