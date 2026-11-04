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
    return Match(n, [filtro], [""], garantida=garantida)


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
