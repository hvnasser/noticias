"""Tira notícias antigas que voltam aos feeds (ex: recomendadas de novo pelo
site, às vezes com data nova no RSS).

Duas regras, só com a MESMA matéria (mesmo link), nunca com parecidas:
1. Supabase: o link já foi coletado pela primeira vez há mais de 24 horas
   (olhando só a última semana).
2. Página: a data de publicação declarada pelo site (meta tags) é anterior
   à janela da busca.
Num grupo de matérias sobre o mesmo fato, sai só a matéria antiga; se a
principal sair, a próxima do grupo assume.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .busca import Noticia, chave_link
from .temas import ResultadoTema

FUSO = ZoneInfo("America/Sao_Paulo")


def _filtrar(resultados: list[ResultadoTema], velha) -> list[tuple[Noticia, str]]:
    """Remove as notícias para as quais `velha(n)` devolve um motivo."""
    removidas = []
    for res in resultados:
        ficam = []
        for m in res.noticias:
            fora = [(n, motivo) for n in [m.noticia] + m.relacionadas if (motivo := velha(n))]
            if not fora:
                ficam.append(m)
                continue
            removidas += fora
            saem = {id(n) for n, _ in fora}
            restantes = [n for n in [m.noticia] + m.relacionadas if id(n) not in saem]
            if not restantes:
                continue
            if id(m.noticia) in saem:  # a principal saiu: a próxima assume (resumo era dela)
                m.resumo_ia = ""
                m.id_banco = ""
            m.noticia, m.relacionadas = restantes[0], restantes[1:]
            ficam.append(m)
        res.noticias = ficam
    return removidas


def remover_ja_vistas(resultados: list[ResultadoTema], vistos: dict[str, datetime],
                      agora: datetime | None = None, horas: float = 24) -> list[tuple[Noticia, str]]:
    """Tira as matérias cujo link apareceu pela primeira vez há mais de `horas`."""
    agora = agora or datetime.now(timezone.utc)
    limite = agora - timedelta(hours=horas)

    def velha(n: Noticia) -> str:
        primeira = vistos.get(chave_link(n.link))
        if primeira and primeira < limite:
            return f"já apareceu em {primeira.astimezone(FUSO):%d/%m %H:%M}"
        return ""

    return _filtrar(resultados, velha)


def remover_datas_antigas(resultados: list[ResultadoTema], agora: datetime | None = None,
                          horas: float = 24) -> list[tuple[Noticia, str]]:
    """Tira as matérias cuja página declara data anterior à janela da busca.
    A página só informa o dia, então só sai o que é de antes do dia do limite."""
    agora = agora or datetime.now(timezone.utc)
    dia_limite = (agora - timedelta(hours=horas)).astimezone(FUSO).date()
    hoje = agora.astimezone(FUSO).date()

    def velha(n: Noticia) -> str:
        try:
            d = date.fromisoformat(n.data_pagina[:10]) if n.data_pagina else None
        except ValueError:
            return ""
        if d and d < dia_limite and (hoje - d).days <= 365 * 5:
            return f"página diz {d:%d/%m/%Y}"
        return ""

    return _filtrar(resultados, velha)
