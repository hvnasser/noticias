import json
import re
from datetime import datetime, timezone

from n8news.banco import id_noticia
from n8news.busca import Noticia
from n8news.painel import gerar_painel, montar_dados
from n8news.temas import Filtro, Match, ResultadoTema, Tema

AGORA = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
TEMA = Tema("credito", "Crédito", [Filtro("Recuperação judicial", ["x"], separar_brasil=False),
                                   Filtro("Spreads corporativos", ["y"])])


def historico_linha(link, titulo, resumo="", filtro="Spreads corporativos", paises=()):
    return {"tema": "credito", "filtros": [filtro], "criado_em": "2026-10-19T10:00:00+00:00",
            "noticias": {"id": id_noticia(link), "titulo": titulo, "link": link, "fontes": ["Valor"],
                         "outros_links": [{"fonte": "G1", "link": "https://g1/x", "titulo": "t"}],
                         "paises": list(paises), "publicado_em": "2026-10-19T09:00:00+00:00",
                         "coletado_em": "2026-10-19T10:00:00+00:00", "resumo_ia": resumo}}


def test_junta_historico_e_execucao_atual():
    hist = [historico_linha("https://valor/a", "Debêntures recuam", "Resumo antigo"),
            historico_linha("https://ft/b", "Gilts rally as worries ease", "Resumo", paises=["GBR"])]
    n = Noticia("Debêntures recuam", "https://valor/a?utm_source=rss", "", "Valor", "economia",
                datetime(2026, 10, 19, 9, tzinfo=timezone.utc), ["Valor"], ["economia"])
    atual = [ResultadoTema(TEMA, [Match(n, ["Spreads corporativos"], [""], resumo_ia="Resumo novo", regiao="brasil")])]
    d = montar_dados([TEMA], atual, hist, AGORA)
    assert len(d["noticias"]) == 2  # mesma notícia (link com utm) não duplica
    por_titulo = {i["titulo"]: i for i in d["noticias"]}
    assert por_titulo["Debêntures recuam"]["resumo"] == "Resumo novo"
    assert por_titulo["Debêntures recuam"]["coletado"] == "2026-10-19T10:00:00+00:00"  # mantém 1ª coleta
    assert por_titulo["Gilts rally as worries ease"]["regiao"] == "mundo"
    assert por_titulo["Gilts rally as worries ease"]["outros"] == [{"fonte": "G1", "link": "https://g1/x"}]
    assert d["temas"][0]["filtros"] == [{"nome": "Recuperação judicial", "separar": False},
                                        {"nome": "Spreads corporativos", "separar": True}]


def test_resumo_igual_ao_titulo_fica_vazio():
    d = montar_dados([TEMA], [], [historico_linha("https://x", "Só título", "Só título")], AGORA)
    assert d["noticias"][0]["resumo"] == ""


def test_gera_html_seguro_sem_texto_da_materia(tmp_path):
    n = Noticia("Título </script><script>alert(1)</script>", "https://x", "trecho", "Valor", "economia",
                AGORA, ["Valor"], ["economia"])
    n.texto = "TEXTO COMPLETO DA MATÉRIA"
    atual = [ResultadoTema(TEMA, [Match(n, ["Spreads corporativos"], [""], resumo_ia="r")])]
    arquivo, qtd, avisos = gerar_painel(tmp_path, [TEMA], atual, banco=None)
    html = arquivo.read_text()
    assert qtd == 1 and avisos == []
    assert "TEXTO COMPLETO" not in html
    assert html.count("</script>") == 2  # só as duas tags reais
    bruto = re.search(r'<script id="dados" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(bruto)["noticias"][0]["titulo"].startswith("Título </script>")
    assert "<title>Notícias</title>" in html and "n8n" not in html.lower() and (tmp_path / ".nojekyll").exists()


def test_falha_no_historico_vira_aviso(tmp_path):
    class BancoQuebrado:
        def historico(self, desde):
            raise RuntimeError("fora do ar")
    _, qtd, avisos = gerar_painel(tmp_path, [TEMA], [], banco=BancoQuebrado())
    assert qtd == 0 and "fora do ar" in avisos[0]


def test_consulta_historico_pagina():
    from n8news.banco import Supabase
    chamadas = []

    def enviar(metodo, caminho, corpo, headers):
        chamadas.append(caminho)
        return [{"x": 1}] * (1000 if len(chamadas) == 1 else 3)

    linhas = Supabase("https://a.supabase.co", "k", enviar=enviar).historico(AGORA)
    assert len(linhas) == 1003
    assert "noticias_temas?select=tema,filtros,criado_em,noticias(" in chamadas[0]
    assert "criado_em=gte.2026-10-20T18:00:00Z" in chamadas[0] and "offset=1000" in chamadas[1]


def _itens(titulos, filtro="Recuperação judicial"):
    return [{"tema": "credito", "filtro": filtro, "titulo": t, "link": f"https://x/{n}", "fontes": [f"F{n}"],
             "outros": [], "paises": [], "publicado": f"2026-10-20T{10 + n:02d}:00:00+00:00",
             "coletado": None, "resumo": "", "regiao": "brasil", "id": str(n)}
            for n, t in enumerate(titulos)]


def test_painel_junta_mesmo_assunto_do_historico():
    from n8news.painel import agrupar_assuntos
    itens = _itens([
        "Justiça encerra recuperação judicial da Americanas",
        "Americanas sobe forte na bolsa com encerramento da RJ",
        "Ações da Americanas disparam mais de 20% após fim da recuperação judicial",
        "Ri Happy pede recuperação extrajudicial",
        "MP opina a favor de recuperação extrajudicial da Ri Happy",
        "Caso da Oncoclínicas testa regras da recuperação extrajudicial",
        "Varejista de móveis pede recuperação judicial",
    ])
    grupos = {g["titulo"]: g for g in agrupar_assuntos(itens, TEMA)}
    assert len(grupos) == 4
    americanas = grupos["Ações da Americanas disparam mais de 20% após fim da recuperação judicial"]  # a mais recente
    assert len(americanas["mais"]) == 2
    assert {o["fonte"] for o in americanas["outros"]} == set()
    assert len(grupos["MP opina a favor de recuperação extrajudicial da Ri Happy"]["mais"]) == 1


def test_painel_nao_junta_mesmo_nome_em_secoes_diferentes_ou_longe_no_tempo():
    from n8news.painel import agrupar_assuntos
    a = _itens(["Petrobras capta US$ 2 bilhões em bonds"], "Spreads corporativos")
    b = _itens(["Moody's eleva rating da Petrobras"], "Recuperação judicial")
    b[0]["link"] = "https://y/1"
    assert len(agrupar_assuntos(a + b, TEMA)) == 2
    c = _itens(["Americanas pede recuperação judicial", "Americanas aprova plano com credores"])
    c[1]["publicado"] = "2026-11-10T10:00:00+00:00"
    assert len(agrupar_assuntos(c, TEMA)) == 2


def test_painel_junta_mesmo_link_entre_execucoes():
    from n8news.painel import agrupar_assuntos
    a, b = _itens(["Título de um jeito", "Outro título totalmente diferente"])
    b["outros"] = [{"fonte": "Valor", "link": "https://x/0?utm_source=rss"}]
    grupos = agrupar_assuntos([a, b], TEMA)
    assert len(grupos) == 1 and grupos[0]["outros"] == [{"fonte": "Valor", "link": "https://x/0?utm_source=rss"}]


def test_botao_atualizar_aponta_para_o_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "fulano/noticias")
    arquivo, _, _ = gerar_painel(tmp_path, [TEMA], [], banco=None)
    assert "https://github.com/fulano/noticias/actions/workflows/buscar-noticias.yml" in arquivo.read_text()
    monkeypatch.delenv("GITHUB_REPOSITORY")
    arquivo, _, _ = gerar_painel(tmp_path, [TEMA], [], banco=None)
    assert '"atualizar": ""' in arquivo.read_text()


def test_painel_tem_abas_por_tema(tmp_path):
    from n8news.temas import carregar_temas
    arquivo, _, _ = gerar_painel(tmp_path, carregar_temas("temas.yaml"), [], banco=None)
    html = arquivo.read_text()
    assert 'id="abas"' in html and '"id": "bancos_centrais"' in html and 'id="tema"' not in html
