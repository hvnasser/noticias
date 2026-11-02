import json

import pytest

from n8news.busca import Noticia
from n8news.paises import carregar_paises
from n8news.resumo import ConfigIA, Tarefa, resumir_temas
from n8news.temas import carregar_temas, classificar
from n8news.texto import normalizar

from test_paises_resumo import FakeIA, ecoa


def n(titulo, categoria="economia", autor="", fonte="X"):
    x = Noticia(titulo=titulo, link="https://x/" + titulo, resumo="", fonte=fonte, categoria=categoria,
                publicado_em=None, fontes=[fonte], categorias=[categoria])
    x.autor = autor
    return x


@pytest.fixture(scope="module")
def paises():
    return carregar_paises("paises.yaml")


@pytest.fixture(scope="module")
def bc():
    return next(t for t in carregar_temas("temas.yaml") if t.id == "bancos_centrais")


def secoes(res):
    return {m.noticia.titulo: m.filtros[0] for m in res.noticias}


def test_cada_banco_central_na_sua_secao(bc, paises):
    res = classificar([
        n("Copom mantém Selic em 15% e sinaliza cautela"),
        n("BC vende US$ 1 bilhão em leilão de câmbio"),
        n("Banco Central Europeu mantém juros e Lagarde fala em cautela"),
        n("Fed holds rates; Powell says inflation still elevated", "mundo"),
        n("BOE's Bailey says more cuts likely", "mundo"),
        n("Bank of Japan raises rates to 1%", "mundo"),
        n("PBOC cuts reserve requirement ratio", "mundo"),
    ], [bc], paises)[0]
    assert secoes(res) == {
        "Copom mantém Selic em 15% e sinaliza cautela": "Banco Central do Brasil",
        "BC vende US$ 1 bilhão em leilão de câmbio": "Banco Central do Brasil",
        "Banco Central Europeu mantém juros e Lagarde fala em cautela": "BCE",
        "Fed holds rates; Powell says inflation still elevated": "Fed",
        "BOE's Bailey says more cuts likely": "Banco da Inglaterra",
        "Bank of Japan raises rates to 1%": "Banco do Japão",
        "PBOC cuts reserve requirement ratio": "Banco do Povo da China",
    }


def test_fora_do_tema(bc, paises):
    res = classificar([
        n("Voters fed up with inflation turn to opposition", "mundo"),
        n("Banco central argentino eleva juros para conter o peso"),
        n("Americanas encerra recuperação judicial"),
    ], [bc], paises)[0]
    assert res.noticias == []


def test_colunistas_entram_por_busca_ou_autor(bc, paises):
    res = classificar([
        n("Officials split on December move", "colunista_timiraos", fonte="WSJ"),  # busca pelo nome
        n("A armadilha da comunicação", autor="Alex Ribeiro", fonte="Valor Econômico"),  # autor no RSS
        n("WSJ's Nick Timiraos on what to expect", "colunista_timiraos", fonte="MarketWatch"),  # outro site: não
        n("Outra coluna sem relação", autor="Fulano"),
    ], [bc], paises)[0]
    assert secoes(res) == {
        "Officials split on December move": "Fed",                     # Timiraos -> seção do Fed
        "A armadilha da comunicação": "Banco Central do Brasil",       # Alex Ribeiro -> seção do BCB
    }
    # Só o autor confirmado entra direto; o que veio da busca pelo nome a IA confere
    assert {m.noticia.titulo: m.garantida for m in res.noticias} == {
        "Officials split on December move": False, "A armadilha da comunicação": True}


def test_ia_nao_tira_colunista_do_tema(bc, paises):
    res = classificar([n("A armadilha da comunicação", autor="Alex Ribeiro", fonte="Valor Econômico"),
                       n("Talarico rejects left-right politics", "colunista_timiraos", fonte="WSJ"),
                       n("Fed's Waller backs a December rate cut", "mundo")], [bc], paises)
    ia = FakeIA([ecoa("relevante", False), ecoa("resumo", "r")])
    resumir_temas(res, ConfigIA(tarefas={"relevancia": Tarefa("m"), "resumo": Tarefa("m")}), ia)
    # A IA disse "fora do tema" para todas: só fica o colunista confirmado
    assert [m.noticia.titulo for m in res[0].noticias] == ["A armadilha da comunicação"]
    # a IA recebe a descrição do tema
    entrada = json.loads(ia.chamadas[0]["usuario"])
    assert entrada["tema"] == "Bancos centrais" and "Copom" in entrada["descricao"]


def test_possessivo_ingles_separa_palavra():
    assert normalizar("BoE's Bailey") == " boe s bailey "
    assert normalizar("People’s Bank") == " people s bank "
    assert normalizar("d'água") == " dagua "


def test_pagina_de_autor_vira_noticias_com_autor():
    from datetime import datetime, timezone
    from n8news.busca import Config, Feed, buscar_noticias
    html = """<html><body>
      <a href="https://valor.globo.com/financas/noticia/2026/11/02/analise-copom-teste.ghtml"><h2>Análise: Copom enfrenta teste como única âncora</h2></a>
      <a href="https://valor.globo.com/financas/noticia/2026/11/02/analise-copom-teste.ghtml">Análise: Copom enfrenta teste como única âncora</a>
      <a href="https://valor.globo.com/financas/noticia/2026/10/20/velha.ghtml">Matéria antiga de outubro sobre juros</a>
      <a href="https://valor.globo.com/opiniao/">Opinião</a>
    </body></html>"""
    feed = Feed("Valor Econômico", "colunista_alex", "https://valor.globo.com/opiniao/alex-ribeiro/",
                tipo="pagina", autor="Alex Ribeiro")
    agora = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
    r = buscar_noticias(Config([feed]), agora, baixar_fn=lambda url, t: html.encode())
    assert [(n.titulo, n.autor) for n in r.noticias] == [("Análise: Copom enfrenta teste como única âncora", "Alex Ribeiro")]
    assert r.noticias[0].publicado_em == datetime(2026, 11, 2, 15, tzinfo=timezone.utc)


def test_busca_pelo_nome_e_conferida_mesmo_com_resumo_gravado(bc, paises):
    res = classificar([n("Talarico rejects left-right politics", "colunista_timiraos", fonte="WSJ"),
                       n("Fed officials split on December cut", "colunista_timiraos", fonte="WSJ")], [bc], paises)
    for m in res[0].noticias:
        m.resumo_ia = "resumo já gravado"
    assert [m.so_busca for m in res[0].noticias] == [True, False]  # a 2ª também casa com "Fed"
    ia = FakeIA([ecoa("relevante", False)])
    resumir_temas(res, ConfigIA(tarefas={"relevancia": Tarefa("m"), "resumo": Tarefa("m")}), ia)
    assert [m.noticia.titulo for m in res[0].noticias] == ["Fed officials split on December cut"]


def test_martin_wolf_vai_para_a_secao_do_assunto(bc, paises):
    from n8news.busca import Config, Feed, buscar_noticias
    from datetime import datetime, timezone
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>Martin Wolf</title>
      <item><title>The Bank of England must not repeat its mistakes</title><link>https://www.ft.com/content/a</link>
        <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>
      <item><title>Britain cannot build its future by returning to the 1970s</title><link>https://www.ft.com/content/b</link>
        <pubDate>Mon, 02 Nov 2026 09:00:00 GMT</pubDate></item>
    </channel></rss>"""
    feed = Feed("Financial Times", "colunista_wolf", "https://www.ft.com/martin-wolf?format=rss", autor="Martin Wolf")
    r = buscar_noticias(Config([feed]), datetime(2026, 11, 2, 18, tzinfo=timezone.utc), baixar_fn=lambda u, t: rss.encode())
    assert {x.autor for x in r.noticias} == {"Martin Wolf"}
    res = classificar(r.noticias, [bc], paises)[0]
    assert secoes(res) == {"The Bank of England must not repeat its mistakes": "Banco da Inglaterra"}
