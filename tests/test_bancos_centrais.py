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
        n("Officials split on December move", "colunistas_bc", fonte="WSJ"),  # busca pelo nome
        n("A armadilha da comunicação", autor="Alex Ribeiro", fonte="Valor Econômico"),  # autor no RSS
        n("WSJ's Nick Timiraos on what to expect", "colunistas_bc", fonte="MarketWatch"),  # outro site: não
        n("Outra coluna sem relação", autor="Fulano"),
    ], [bc], paises)[0]
    assert secoes(res) == {
        "Officials split on December move": "Colunistas: Alex Ribeiro e Nick Timiraos",
        "A armadilha da comunicação": "Colunistas: Alex Ribeiro e Nick Timiraos",
    }
    assert all(m.garantida for m in res.noticias)


def test_ia_nao_tira_colunista_do_tema(bc, paises):
    res = classificar([n("Officials split on December move", "colunistas_bc", fonte="WSJ"),
                       n("Fed's Waller backs a December rate cut", "mundo")], [bc], paises)
    ia = FakeIA([ecoa("relevante", False), ecoa("resumo", "r")])
    resumir_temas(res, ConfigIA(tarefas={"relevancia": Tarefa("m"), "resumo": Tarefa("m")}), ia)
    assert [m.noticia.titulo for m in res[0].noticias] == ["Officials split on December move"]
    # a IA recebe a descrição do tema
    entrada = json.loads(ia.chamadas[0]["usuario"])
    assert entrada["tema"] == "Bancos centrais" and "Copom" in entrada["descricao"]


def test_possessivo_ingles_separa_palavra():
    assert normalizar("BoE's Bailey") == " boe s bailey "
    assert normalizar("People’s Bank") == " people s bank "
    assert normalizar("d'água") == " dagua "
