"""Baixa a página de cada notícia e extrai o texto principal (trafilatura).

Em sites com paywall (Valor, FT, WSJ) normalmente só vem o começo da matéria.
Links do Google News passam por um redirecionamento em JavaScript e não são
baixados; essas notícias ficam só com o trecho do RSS.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import trafilatura

from .busca import Noticia, baixar

MAX_CARACTERES = 20000


def extrair(html: bytes | str) -> tuple[str, str]:
    """Devolve (texto principal, autor) da página."""
    doc = trafilatura.bare_extraction(html, with_metadata=True, include_comments=False,
                                      include_tables=False, favor_precision=True)
    if not doc:
        return "", ""
    return (getattr(doc, "text", "") or "").strip(), (getattr(doc, "author", "") or "").strip()


def extrair_texto(html: bytes | str) -> str:
    return extrair(html)[0]


def _baixar_pagina(n: Noticia, timeout: float, baixar_fn) -> bytes | str | None:
    if "news.google.com" in n.link:
        return None
    try:
        return baixar_fn(n.link, timeout)
    except Exception:  # noqa: BLE001 - página fora do ar não pode parar o robô
        return None


def _extrair_seguro(pagina) -> tuple[str, str]:
    if not pagina:
        return "", ""
    try:
        texto, autor = extrair(pagina)
        return texto[:MAX_CARACTERES], autor
    except Exception:  # noqa: BLE001
        return "", ""


def baixar_textos(noticias: list[Noticia], timeout: float = 15, baixar_fn=baixar) -> int:
    """Preenche `texto` das notícias que ainda não têm. Devolve quantas ganharam texto.
    Baixa as páginas em paralelo, mas extrai o texto uma de cada vez: a
    extração (lxml) em várias threads pode derrubar o Python ("free(): invalid
    pointer")."""
    pendentes = [n for n in noticias if not n.texto]
    with ThreadPoolExecutor(max_workers=8) as pool:
        paginas = list(pool.map(lambda n: _baixar_pagina(n, timeout, baixar_fn), pendentes))
    textos = [_extrair_seguro(p) for p in paginas]
    ok = 0
    for n, (texto, autor) in zip(pendentes, textos):
        if autor and not n.autor:
            n.autor = autor
        # Só vale se trouxer mais que o trecho do RSS
        if len(texto) > len(n.resumo) + 80:
            n.texto = texto
            ok += 1
    return ok
