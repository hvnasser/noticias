import json
from types import SimpleNamespace

import pytest

from n8news.busca import Noticia
from n8news.paises import carregar_paises, detectar, expandir_permitidos
from n8news.ia import ErroCredencial, ErroIA, OpenRouter
from n8news.resumo import (
    SCHEMA_JUNTO, SCHEMA_RELEVANCIA, SCHEMA_RESUMO, ConfigIA, Tarefa, carregar_config_ia, resumir_temas,
)
from n8news.temas import carregar_temas, classificar
from n8news.texto import normalizar


def noticia(titulo, resumo="", categoria="economia"):
    return Noticia(titulo=titulo, link="https://x/" + titulo, resumo=resumo, fonte="X",
                   categoria=categoria, publicado_em=None, fontes=["X"], categorias=[categoria])


@pytest.fixture(scope="module")
def paises():
    return carregar_paises("paises.yaml")


@pytest.fixture(scope="module")
def credito():
    return next(t for t in carregar_temas("temas.yaml") if t.id == "credito")


@pytest.mark.parametrize("texto, esperado", [
    ("Ghana reaches debt restructuring deal", {"GHA"}),
    ("Lanka's debt restructuring 99% complete", {"LKA"}),
    ("French-German bond spread widens", {"FRA", "DEU"}),
    ("Risco-país do Brasil cai", {"BRA"}),
    ("Argentina paga cupom de bônus", {"ARG"}),
    ("Russell 2000 sobe", set()),
    ("Ri Happy pede recuperação extrajudicial", set()),
])
def test_detecta_paises(paises, texto, esperado):
    assert {p.codigo for p in detectar(paises, normalizar(texto))} == esperado


def test_grupos(paises):
    permitidos = expandir_permitidos(paises, ["g10", "em"])
    assert {"EUA", "GBR", "JPN", "BRA", "MEX", "CHN", "ARG"} <= permitidos
    assert not {"GHA", "LKA", "PAK", "EGY", "UKR"} & permitidos
    assert expandir_permitidos(paises, ["GHA"]) == {"GHA"}


def test_tema_tira_paises_fora_da_lista(credito, paises):
    noticias = [
        noticia("Ghana reaches debt restructuring deal with bondholders"),
        noticia("Lanka's debt restructuring 99% complete"),
        noticia("Argentina e FMI discutem reestruturação de dívida"),
        noticia("Ri Happy pede recuperação extrajudicial"),
        noticia("Pakistan and China agree debt restructuring"),  # China está na lista
    ]
    res = classificar(noticias, [credito], paises)[0]
    titulos = {m.noticia.titulo for m in res.noticias}
    assert titulos == {
        "Argentina e FMI discutem reestruturação de dívida",
        "Ri Happy pede recuperação extrajudicial",
        "Pakistan and China agree debt restructuring",
    }
    motivos = {n.titulo: m for n, m in res.excluidas}
    assert motivos["Ghana reaches debt restructuring deal with bondholders"] == "país fora da lista: GHA"


class FakeIA:
    """Imita o cliente de IA (OpenRouter/Anthropic) e guarda as chamadas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def json(self, **kwargs):
        self.chamadas.append(kwargs)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r(kwargs) if callable(r) else r


def junto(relevante=True, resumo="resumo da IA", opiniao=False, autor=""):
    """Resposta da chamada única (relevância + resumo) para todos os ids recebidos."""
    def f(kwargs):
        ids = [n["id"] for n in json.loads(kwargs["usuario"])["noticias"]]
        val = lambda v, i: v(i) if callable(v) else v
        return {"itens": [{"id": i, "relevante": val(relevante, i), "opiniao": opiniao, "autor": autor,
                           "resumo": val(resumo, i)} for i in ids]}
    return f


def ecoa(campo, valor):
    """Resposta que devolve `campo` para todos os ids recebidos."""
    def f(kwargs):
        ids = [n["id"] for n in json.loads(kwargs["usuario"])["noticias"]]
        return {"itens": [{"id": i, campo: valor(i) if callable(valor) else valor} for i in ids]}
    return f


def test_relevancia_e_resumo_numa_chamada_so(credito, paises):
    noticias = [
        noticia("Ri Happy pede recuperação extrajudicial", "A Ri Happy pediu RE para renegociar R$ 827 mi."),
        noticia("Empresa X pede recuperação judicial em tom de piada", "texto"),
    ]
    res = classificar(noticias, [credito], paises)
    ia = FakeIA([{"itens": [
        {"id": 0, "relevante": True, "opiniao": False, "autor": "", "resumo": "Ri Happy pede RE para renegociar R$ 827 milhões."},
        {"id": 1, "relevante": False, "opiniao": False, "autor": "", "resumo": ""}]}])
    cfg = ConfigIA(tarefas={"relevancia": Tarefa("modelo-barato"), "resumo": Tarefa("modelo-bom", esforco="low")})
    assert resumir_temas(res, cfg, ia) == []
    assert [m.resumo_ia for m in res[0].noticias] == ["Ri Happy pede RE para renegociar R$ 827 milhões."]
    assert res[0].excluidas[-1][1] == "IA: fora do tema"
    # Uma chamada só, com o modelo do resumo; cada notícia vai uma vez, com o trecho
    (chamada,) = ia.chamadas
    assert chamada["modelo"] == "modelo-bom" and chamada["esforco"] == "low" and chamada["schema"] is SCHEMA_JUNTO
    assert json.loads(chamada["usuario"])["noticias"][0]["trecho"].startswith("A Ri Happy")


def test_so_relevancia_ligada_usa_o_modelo_dela(credito, paises):
    res = classificar([noticia("Ri Happy pede recuperação extrajudicial", "trecho")], [credito], paises)
    ia = FakeIA([ecoa("relevante", True)])
    cfg = ConfigIA(tarefas={"relevancia": Tarefa("barato"), "resumo": Tarefa("y", ativo=False)})
    resumir_temas(res, cfg, ia)
    assert [(c["modelo"], c["schema"]) for c in ia.chamadas] == [("barato", SCHEMA_RELEVANCIA)]


def test_tema_troca_modelo_de_uma_tarefa(credito, paises):
    credito.ia = {"resumo": {"modelo": "modelo-do-tema"}}
    try:
        res = classificar([noticia("Ri Happy pede recuperação extrajudicial", "trecho")], [credito], paises)
        ia = FakeIA([junto()])
        cfg = ConfigIA(tarefas={"relevancia": Tarefa("barato"), "resumo": Tarefa("padrao", esforco="low")})
        resumir_temas(res, cfg, ia)
        assert [c["modelo"] for c in ia.chamadas] == ["modelo-do-tema"]
        assert ia.chamadas[0]["esforco"] == "low"  # o resto vem da tarefa padrão
    finally:
        credito.ia = {}


def test_tarefa_desligada_e_noticia_ja_resumida(credito, paises):
    res = classificar([noticia("Ri Happy pede recuperação extrajudicial", "trecho"),
                       noticia("Beta Ltda pede recuperação judicial", "trecho")], [credito], paises)
    res[0].noticias[0].resumo_ia = "já tinha"  # veio do banco
    ia = FakeIA([ecoa("resumo", "novo")])
    cfg = ConfigIA(tarefas={"relevancia": Tarefa("x", ativo=False), "resumo": Tarefa("y")})
    resumir_temas(res, cfg, ia)
    assert len(ia.chamadas) == 1  # só resumo, só da notícia nova
    assert len(json.loads(ia.chamadas[0]["usuario"])["noticias"]) == 1
    assert sorted(m.resumo_ia for m in res[0].noticias) == ["já tinha", "novo"]


def test_erros_nao_quebram(credito, paises):
    res = classificar([noticia("Ri Happy pede recuperação extrajudicial", "trecho")], [credito], paises)
    avisos = resumir_temas(res, ConfigIA(), FakeIA([ErroIA("modelo não existe")]))
    assert len(avisos) == 1 and "modelo não existe" in avisos[0]
    assert len(res[0].noticias) == 1 and res[0].noticias[0].resumo_ia == ""

    avisos = resumir_temas(res, ConfigIA(), FakeIA([ErroCredencial("chave inválida")]))
    assert avisos == ["IA não rodou: chave inválida"]


def test_so_titulo_vira_resumo_sem_ia(credito, paises):
    res = classificar([noticia("Ares Warns of Australian Private Credit Stress"),
                       noticia("Ri Happy pede recuperação extrajudicial", "A Ri Happy pediu RE.")], [credito], paises)
    ia = FakeIA([junto(resumo="resumo da IA")])
    resumir_temas(res, ConfigIA(), ia)
    resumos = {m.noticia.titulo: m.resumo_ia for m in res[0].noticias}
    # só título: o resumo é o próprio título, mesmo se a IA escrever algo
    assert resumos["Ares Warns of Australian Private Credit Stress"] == "Ares Warns of Australian Private Credit Stress"
    assert resumos["Ri Happy pede recuperação extrajudicial"] == "resumo da IA"
    assert len(ia.chamadas) == 1 and len(json.loads(ia.chamadas[0]["usuario"])["noticias"]) == 2


def test_so_titulo_sem_nenhuma_chamada_de_resumo(credito, paises):
    res = classificar([noticia("Beta Ltda pede recuperação judicial")], [credito], paises)
    ia = FakeIA([junto(resumo="")])
    assert resumir_temas(res, ConfigIA(), ia) == []
    assert len(ia.chamadas) == 1  # uma chamada (relevância), sem resumo à parte
    assert res[0].noticias[0].resumo_ia == "Beta Ltda pede recuperação judicial"


def test_opiniao_ganha_prefixo_com_autor(credito, paises):
    n1 = noticia("Ajuste fiscal requer liderança política e dívida pública controlada", "As contas públicas...")
    n1.autor = "Fulano de Tal"
    n2 = noticia("Opinion | The sovereign debt reckoning is coming")  # só título
    n3 = noticia("Ri Happy pede recuperação extrajudicial", "A Ri Happy pediu RE.")
    res = classificar([n1, n2, n3], [credito], paises)
    titulos = [m.noticia.titulo for m in res[0].noticias]

    def relev(kwargs):
        itens = []
        for n in json.loads(kwargs["usuario"])["noticias"]:
            t = n["titulo"]
            itens.append({"id": n["id"], "relevante": True,
                          "opiniao": not t.startswith("Ri Happy"),
                          "autor": n.get("autor", "") if t.startswith("Ajuste") else "",
                          "resumo": "Resumo."})
        return {"itens": itens}

    ia = FakeIA([relev])
    resumir_temas(res, ConfigIA(), ia)
    r = {m.noticia.titulo: m.resumo_ia for m in res[0].noticias}
    assert r[n1.titulo] == "Opinião — Fulano de Tal: Resumo."
    assert r[n2.titulo] == "Opinião (autor não identificado): Opinion | The sovereign debt reckoning is coming"
    assert r[n3.titulo] == "Resumo."
    enviado = json.loads(ia.chamadas[0]["usuario"])["noticias"]
    assert any(n.get("autor") == "Fulano de Tal" for n in enviado)
    assert len(titulos) == 3


def test_sem_chave_openrouter(credito, paises, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    res = classificar([noticia("Ri Happy pede recuperação extrajudicial")], [credito], paises)
    avisos = resumir_temas(res, ConfigIA())
    assert "OPENROUTER_API_KEY" in avisos[0]


def test_lotes(credito, paises):
    noticias = [noticia(f"Empresa{i} pede recuperação judicial", "trecho") for i in range(5)]
    res = classificar(noticias, [credito], paises)
    n = len(res[0].noticias)
    lotes = (n + 1) // 2
    ia = FakeIA([ecoa("resumo", lambda i: f"r{i}")] * lotes)
    cfg = ConfigIA(tarefas={"relevancia": Tarefa("x", ativo=False), "resumo": Tarefa("y")}, max_por_chamada=2)
    resumir_temas(res, cfg, ia)
    assert len(ia.chamadas) == lotes
    assert all(m.resumo_ia for m in res[0].noticias)


def test_config_do_temas_yaml():
    cfg = carregar_config_ia("temas.yaml")
    assert cfg.provedor == "openrouter"
    assert "/" in cfg.tarefa("relevancia").modelo and "/" in cfg.tarefa("resumo").modelo


# ------------------------------------------------------------ OpenRouter

def resposta_or(conteudo, status=200):
    return status, {"choices": [{"message": {"content": conteudo}, "finish_reason": "stop"}]}


def test_openrouter_monta_pedido_e_le_json():
    enviados = []

    def enviar(corpo):
        enviados.append(corpo)
        return resposta_or('```json\n{"itens": [{"id": 0, "resumo": "ok"}]}\n```')

    r = OpenRouter("sk-or-x", enviar=enviar).json(modelo="google/gemini-3.6-flash", sistema="S", usuario="U",
                                                  schema=SCHEMA_RESUMO, esforco="low")
    assert r == {"itens": [{"id": 0, "resumo": "ok"}]}
    corpo = enviados[0]
    assert corpo["model"] == "google/gemini-3.6-flash"
    assert corpo["response_format"]["json_schema"]["schema"] is SCHEMA_RESUMO
    assert corpo["reasoning"] == {"effort": "low"}
    assert [m["role"] for m in corpo["messages"]] == ["system", "user"]


def test_openrouter_modelo_sem_saida_estruturada():
    enviados = []

    def enviar(corpo):
        enviados.append(dict(corpo))
        if "response_format" in corpo:
            return 400, {"error": {"message": "response_format json_schema is not supported"}}
        return resposta_or('Aqui está: {"itens": []}')

    assert OpenRouter("k", enviar=enviar).json(modelo="m", sistema="s", usuario="u", schema={}) == {"itens": []}
    assert "response_format" in enviados[0] and "response_format" not in enviados[1]
    assert "reasoning" not in enviados[0]


def test_openrouter_erros():
    with pytest.raises(ErroCredencial):
        OpenRouter("k", enviar=lambda c: (401, {"error": {"message": "No auth"}})).json(
            modelo="m", sistema="s", usuario="u", schema={})
    with pytest.raises(ErroIA, match="not a valid model"):
        OpenRouter("k", enviar=lambda c: (400, {"error": {"message": "x is not a valid model ID"}})).json(
            modelo="x", sistema="s", usuario="u", schema={})
    with pytest.raises(ErroIA, match="resposta inválida"):
        OpenRouter("k", enviar=lambda c: resposta_or("não sei")).json(modelo="m", sistema="s", usuario="u", schema={})
    # erro dentro de um 200 (acontece no OpenRouter)
    with pytest.raises(ErroIA):
        OpenRouter("k", enviar=lambda c: (200, {"error": {"message": "provider down"}})).json(
            modelo="m", sistema="s", usuario="u", schema={})
