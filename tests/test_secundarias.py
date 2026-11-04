import json

from n8news.busca import Noticia
from n8news.resumo import ConfigIA, Tarefa, filtrar_secundarias
from n8news.temas import Filtro, Match, Prioridade, ResultadoTema, Tema

PRIO = Prioridade.de_dict({"brasil": ["Valor", "Estadão", ["O Globo", "G1"], "Folha"],
                           "mundo": [["WSJ", "Wall Street Journal"], "Reuters"]})
TEMA = Tema("bc", "Bancos centrais", [Filtro("BCB", ["x"]), Filtro("Fed", ["y"])], descricao="política monetária")
CFG = ConfigIA(tarefas={"secundarias": Tarefa("modelo-barato", esforco="low")})


def m(titulo, fonte, filtro="BCB", resumo="", garantida=False):
    n = Noticia(titulo, "https://x/" + titulo, resumo, fonte, "economia", None, [fonte], ["economia"])
    return Match(n, [filtro], [""], garantida=garantida, regiao="brasil" if filtro == "BCB" else "mundo")


class IA:
    """Responde conforme o título e guarda o que recebeu."""

    def __init__(self, etapa1, etapa2=None):
        self.etapa1, self.etapa2, self.chamadas = etapa1, etapa2 or {}, []

    def json(self, **kw):
        entrada = json.loads(kw["usuario"])
        self.chamadas.append(entrada)
        tabela = self.etapa2 if "trecho" in entrada["candidatas"][0] else self.etapa1
        return {"itens": [{"id": c["id"], "veredito": tabela[c["titulo"]]} for c in entrada["candidatas"]]}


def test_titulo_primeiro_e_texto_so_na_duvida():
    res = [ResultadoTema(TEMA, [
        m("Copom deve manter a Selic, dizem analistas", "Valor Econômico"),
        m("Mercado aposta em manutenção da Selic pelo Copom", "CNN Brasil"),      # repete
        m("BC divulga dados de crédito de setembro", "Money Times"),             # distinta
        m("Galípolo fala em evento em SP", "InfoMoney", resumo="trecho do RSS"),  # dúvida -> lê
        m("Análise: o Copom e a âncora fiscal", "InfoMoney", garantida=True),     # colunista: não entra
    ])]
    baixados = []
    ia = IA(etapa1={"Mercado aposta em manutenção da Selic pelo Copom": "repete",
                    "BC divulga dados de crédito de setembro": "distinta",
                    "Galípolo fala em evento em SP": "duvida"},
            etapa2={"Galípolo fala em evento em SP": "repete"})
    assert filtrar_secundarias(res, CFG, PRIO, ia, baixar_textos=baixados.extend) == []
    assert [x.noticia.titulo for x in res[0].noticias] == [
        "Copom deve manter a Selic, dizem analistas", "BC divulga dados de crédito de setembro",
        "Análise: o Copom e a âncora fiscal"]
    assert {x.titulo for x, motivo in res[0].excluidas if motivo == "IA: repete as fontes principais"} == {
        "Mercado aposta em manutenção da Selic pelo Copom", "Galípolo fala em evento em SP"}
    # 1ª chamada só com títulos (sem trecho); só a dúvida foi baixada e relida
    assert "trecho" not in ia.chamadas[0]["candidatas"][0] and "trecho" not in ia.chamadas[0]["principais"][0]
    assert [n.titulo for n in baixados] == ["Galípolo fala em evento em SP"]
    assert [c["titulo"] for c in ia.chamadas[1]["candidatas"]] == ["Galípolo fala em evento em SP"]
    assert ia.chamadas[0]["principais"] == [{"titulo": "Copom deve manter a Selic, dizem analistas",
                                             "fontes": ["Valor Econômico"]}]


def test_compara_por_secao_e_sem_principal_precisa_de_duas_candidatas():
    res = [ResultadoTema(TEMA, [
        m("Copom mantém Selic", "Folha"),
        m("Fed holds rates", "CNBC", filtro="Fed"),            # sozinha na seção, sem principal: fica sem chamar a IA
    ])]
    ia = IA(etapa1={})
    filtrar_secundarias(res, CFG, PRIO, ia)
    assert ia.chamadas == [] and len(res[0].noticias) == 2


def test_principal_pelo_grupo_e_erro_da_ia_nao_tira_nada():
    principal_no_grupo = m("Copom mantém Selic", "CNN Brasil")
    principal_no_grupo.relacionadas = [Noticia("Copom mantém Selic", "https://g1/x", "", "G1", "brasil", None, ["G1"], ["brasil"])]
    res = [ResultadoTema(TEMA, [principal_no_grupo, m("Outra coisa do BC", "Money Times")])]

    class Quebrada:
        def json(self, **kw):
            from n8news.ia import ErroIA
            raise ErroIA("fora do ar")
    avisos = filtrar_secundarias(res, CFG, PRIO, Quebrada())
    assert len(res[0].noticias) == 2 and "fontes secundárias" in avisos[0]


def test_junta_mesmo_conteudo_com_link_pela_prioridade():
    from n8news.resumo import juntar_mesmo_conteudo

    class IAGrupos:
        def __init__(self, grupos):
            self.grupos, self.chamadas = grupos, []

        def json(self, **kw):
            entrada = json.loads(kw["usuario"])
            self.chamadas.append(entrada)
            return {"itens": [{"id": n["id"], "grupo": self.grupos[n["titulo"]]} for n in entrada["noticias"]]}

    cfg = ConfigIA(tarefas={"agrupar": Tarefa("modelo-barato")})
    res = [ResultadoTema(TEMA, [
        m("Copom deve manter Selic em 13,75%", "Folha"),
        m("Dólar abre em queda à espera do Copom", "Folha"),
        m("Copom deve interromper cortes da Selic", "Valor Econômico"),
        m("Selic: por que o BC pode parar de cortar", "G1"),
        m("Análise: a âncora fiscal", "Valor Econômico", garantida=True),  # colunista: não entra
        m("Fed holds rates", "Reuters", filtro="Fed"),                       # sozinha na seção
    ])]
    ia = IAGrupos({"Copom deve manter Selic em 13,75%": 1, "Copom deve interromper cortes da Selic": 1,
                   "Selic: por que o BC pode parar de cortar": 1, "Dólar abre em queda à espera do Copom": 2})
    assert juntar_mesmo_conteudo(res, cfg, PRIO, ia) == []
    titulos = [x.noticia.titulo for x in res[0].noticias]
    # Um cartão para as 3 prévias, com o Valor como link principal (prioridade), na posição da 1ª
    assert titulos == ["Copom deve interromper cortes da Selic", "Dólar abre em queda à espera do Copom",
                       "Análise: a âncora fiscal", "Fed holds rates"]
    assert [r.fonte for r in res[0].noticias[0].relacionadas] == ["G1", "Folha"]
    # Só a seção com 2+ notícias foi para a IA, só com título e fonte, e sem o colunista
    assert len(ia.chamadas) == 1
    assert {n["titulo"] for n in ia.chamadas[0]["noticias"]} == set(ia.grupos)
    assert set(ia.chamadas[0]["noticias"][0]) == {"id", "titulo", "fontes"}


def test_memoria_evita_chamar_a_ia_de_novo(tmp_path):
    from n8news.memoria import Memoria
    from n8news.resumo import juntar_mesmo_conteudo
    arquivo = tmp_path / "m.json"
    cfg = ConfigIA(tarefas={"secundarias": Tarefa("x"), "agrupar": Tarefa("x")})

    def busca():  # as mesmas matérias voltam a cada busca
        return [ResultadoTema(TEMA, [
            m("Copom deve manter Selic em 13,75%", "Folha"),
            m("Copom deve interromper cortes da Selic", "Valor Econômico"),
            m("Mercado aposta em manutenção da Selic", "CNN Brasil"),   # secundária: repete
            m("BC divulga dados de crédito", "Money Times"),            # secundária: distinta
        ])]

    class IA:
        def __init__(self):
            self.chamadas = 0

        def json(self, **kw):
            self.chamadas += 1
            e = json.loads(kw["usuario"])
            if "candidatas" in e:
                tab = {"Mercado aposta em manutenção da Selic": "repete", "BC divulga dados de crédito": "distinta"}
                return {"itens": [{"id": c["id"], "veredito": tab[c["titulo"]]} for c in e["candidatas"]]}
            return {"itens": [{"id": n["id"], "grupo": 1 if n["titulo"].startswith("Copom") else 2}
                              for n in e["noticias"]]}

    def rodar(ia):
        mem = Memoria(arquivo)
        res = busca()
        filtrar_secundarias(res, cfg, PRIO, ia, memoria=mem)
        juntar_mesmo_conteudo(res, cfg, PRIO, ia, memoria=mem)
        mem.salvar()
        return res

    ia1 = IA()
    r1 = rodar(ia1)
    assert ia1.chamadas == 2  # uma do filtro, uma da junção
    ia2 = IA()
    r2 = rodar(ia2)
    assert ia2.chamadas == 0  # nada novo: vale o que foi decidido antes
    for r in (r1, r2):
        assert [x.noticia.titulo for x in r[0].noticias] == ["Copom deve interromper cortes da Selic",
                                                             "BC divulga dados de crédito"]
        assert [n.fonte for n in r[0].noticias[0].relacionadas] == ["Folha"]

    # Uma matéria nova na seção: a IA é chamada de novo (só a junção; a nova é principal)
    mem = Memoria(arquivo)
    res = busca()
    res[0].noticias.append(m("Galípolo fala sobre inflação", "Estadão"))
    ia3 = IA()
    filtrar_secundarias(res, cfg, PRIO, ia3, memoria=mem)
    juntar_mesmo_conteudo(res, cfg, PRIO, ia3, memoria=mem)
    assert ia3.chamadas == 1


def test_memoria_esquece_depois_de_7_dias(tmp_path):
    from datetime import datetime, timedelta, timezone
    from n8news.memoria import Memoria
    agora = datetime(2026, 11, 4, tzinfo=timezone.utc)
    mem = Memoria(tmp_path / "m.json", agora=agora - timedelta(days=8))
    mem.guardar_veredito("bc", "https://x/a", "repete")
    mem.salvar()
    assert Memoria(tmp_path / "m.json", agora=agora - timedelta(days=1)).veredito("bc", "https://x/a") == "repete"
    assert Memoria(tmp_path / "m.json", agora=agora).veredito("bc", "https://x/a") is None
