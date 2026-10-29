from datetime import datetime, timedelta, timezone

from n8news.artigo import extrair_dados
from n8news.banco import id_match, id_noticia, id_versao, versoes_gravadas
from n8news.busca import Noticia
from n8news.repetidas import remover_datas_antigas, remover_ja_vistas
from n8news.temas import Filtro, Match, ResultadoTema, Tema

AGORA = datetime(2026, 10, 29, 21, 53, tzinfo=timezone.utc)  # 18:53 em Brasília
TEMA = Tema("credito", "Crédito", [Filtro("RJ", ["x"])])


def n(titulo, link, fonte="Valor", data_pagina=""):
    x = Noticia(titulo, link, "", fonte, "economia", AGORA - timedelta(hours=2), [fonte], ["economia"])
    x.data_pagina = data_pagina
    return x


def res(*matches):
    return [ResultadoTema(TEMA, list(matches))]


TITULOS = {"a": "Americanas encerra RJ", "b": "Azul conclui Chapter 11"}


def vistos(**horas_atras):
    """Versões gravadas, a partir de {link_curto: horas atrás} (título de TITULOS)."""
    linhas = [{"id": id_noticia(f"https://valor.com/{k}"), "link": f"https://valor.com/{k}",
               "titulo": TITULOS.get(k, "Ri Happy pede RE"),
               "coletado_em": (AGORA - timedelta(hours=h)).isoformat()} for k, h in horas_atras.items()]
    return versoes_gravadas(linhas)


def test_remove_so_o_mesmo_link_visto_ha_mais_de_24h():
    velha = Match(n("Americanas encerra RJ", "https://valor.com/a?utm_source=rss"), ["RJ"], [""])
    recente = Match(n("Azul conclui Chapter 11", "https://valor.com/b"), ["RJ"], [""])
    parecida = Match(n("Americanas encerra RJ", "https://valor.com/outra"), ["RJ"], [""])  # mesmo título, outro link
    r = res(velha, recente, parecida)
    removidas = remover_ja_vistas(r, vistos(a=30, b=10), AGORA)
    assert [x.titulo for x, _ in removidas] == ["Americanas encerra RJ"]
    assert "já apareceu em" in removidas[0][1] and "mesmo título" in removidas[0][1]
    assert [m.noticia.link for m in r[0].noticias] == ["https://valor.com/b", "https://valor.com/outra"]


def test_no_grupo_sai_so_a_materia_velha_e_a_proxima_assume():
    velha, nova = n("Ri Happy pede RE", "https://valor.com/r"), n("Ri Happy: MP opina", "https://g1.com/c", "G1")
    m = Match(velha, ["RJ"], [""], relacionadas=[nova], resumo_ia="resumo da velha", id_banco="x")
    r = res(m)
    remover_ja_vistas(r, vistos(r=50), AGORA)
    assert r[0].noticias[0].noticia is nova and r[0].noticias[0].relacionadas == []
    assert r[0].noticias[0].resumo_ia == "" and r[0].noticias[0].id_banco == ""


def test_versoes_gravadas_por_link_mais_antiga_primeiro():
    linhas = [{"id": "v2", "link": "https://valor.com/a", "titulo": "T2", "coletado_em": "2026-10-28T10:00:00+00:00",
               "outros_links": [{"link": "https://g1.com/x"}]},
              {"id": "v1", "link": "https://valor.com/a", "titulo": "T1", "coletado_em": "2026-10-27T10:00:00+00:00"},
              {"id": "x", "link": "https://valor.com/b", "coletado_em": None}]
    v = versoes_gravadas(linhas)
    assert list(v) == ["valor.com/a"] and [x["id"] for x in v["valor.com/a"]] == ["v1", "v2"]


def test_mesmo_link_com_titulo_novo_e_versao_nova_e_fica():
    """WSJ: a mesma página atualizada com o mercado de outro dia."""
    link = "https://wsj.com/treasuries"
    linhas = [{"id": id_noticia(link), "link": link, "titulo": "Treasury Yields Mixed as Markets Price In a Fed Hold",
               "coletado_em": (AGORA - timedelta(days=3)).isoformat()}]
    m = Match(n("Treasury Yields Mixed After Fed Hold", link, "WSJ"), ["RJ"], [""])
    r = res(m)
    assert remover_ja_vistas(r, versoes_gravadas(linhas), AGORA) == []
    assert r[0].noticias == [m]
    assert id_match(m) == id_versao(link, "Treasury Yields Mixed After Fed Hold") != id_noticia(link)


def test_versao_nova_ja_gravada_usa_o_mesmo_registro_e_depois_de_24h_sai():
    link = "https://wsj.com/treasuries"
    v2 = id_versao(link, "Yields Fall")
    linhas = [{"id": id_noticia(link), "link": link, "titulo": "Yields Rise", "coletado_em": (AGORA - timedelta(days=3)).isoformat()},
              {"id": v2, "link": link, "titulo": "Yields Fall", "coletado_em": (AGORA - timedelta(hours=5)).isoformat()}]
    m = Match(n("Yields Fall", link, "WSJ"), ["RJ"], [""])
    remover_ja_vistas(res(m), versoes_gravadas(linhas), AGORA)
    assert id_match(m) == v2
    velha = Match(n("Yields Rise", link, "WSJ"), ["RJ"], [""])  # a versão de 3 dias atrás voltou
    r = res(velha)
    assert len(remover_ja_vistas(r, versoes_gravadas(linhas), AGORA)) == 1 and r[0].noticias == []


def test_data_da_pagina_antiga_sai_recente_ou_sem_data_fica():
    r = res(*[Match(n(t, f"https://valor.com/{t}", data_pagina=d), ["RJ"], [""]) for t, d in
              [("velha", "2026-10-20"), ("ontem", "2026-10-28"), ("hoje", "2026-10-29"),
               ("sem", ""), ("invalida", "ontem")]])
    removidas = remover_datas_antigas(r, AGORA)
    assert [x.titulo for x, _ in removidas] == ["velha"]
    assert removidas[0][1] == "página diz 20/10/2026"
    assert [m.noticia.titulo for m in r[0].noticias] == ["ontem", "hoje", "sem", "invalida"]


def test_data_so_das_marcacoes_da_pagina_nunca_do_texto():
    corpo = "<article><h1>T</h1>" + "<p>Texto longo da matéria. Em 3 de janeiro de 2020 algo aconteceu.</p>" * 6 + "</article>"
    com_meta = ('<html><head><meta property="article:published_time" content="2026-10-20T10:00:00-03:00">'
                '<meta property="article:modified_time" content="2026-10-28T10:00:00-03:00"></head>'
                f"<body>{corpo}</body></html>")
    assert extrair_dados(com_meta)[2] == "2026-10-20"  # publicação, não a modificação
    assert extrair_dados(f"<html><body>{corpo}</body></html>")[2] == ""  # não adivinha pelo texto


def test_painel_nao_mostra_data_depois_da_primeira_coleta():
    from n8news.painel import _mais_cedo
    assert _mais_cedo("2026-10-29T10:00:00+00:00", "2026-10-22T10:00:00+00:00") == "2026-10-22T10:00:00+00:00"
    assert _mais_cedo("2026-10-21T10:00:00+00:00", "2026-10-22T10:00:00+00:00") == "2026-10-21T10:00:00+00:00"
    assert _mais_cedo(None, "2026-10-22T10:00:00+00:00") is None
