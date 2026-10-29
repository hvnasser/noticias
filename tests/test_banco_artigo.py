from datetime import datetime, timezone

from n8news.artigo import baixar_textos, extrair_texto
from n8news.banco import Supabase, id_noticia, linha_noticia, reaproveitar
from n8news.busca import Noticia
from n8news.temas import Filtro, Match, ResultadoTema, Tema


def noticia(titulo, link, resumo=""):
    return Noticia(titulo, link, resumo, "G1", "economia",
                   datetime(2026, 10, 19, 13, tzinfo=timezone.utc), ["G1"], ["economia"])


def resultado(*matches):
    return [ResultadoTema(Tema("credito", "Crédito", [Filtro("f", ["x"])]), list(matches))]


class FakeHTTP:
    def __init__(self, respostas=None):
        self.chamadas, self.respostas = [], respostas or {}

    def __call__(self, metodo, caminho, corpo, headers):
        self.chamadas.append((metodo, caminho, corpo, headers))
        return self.respostas.get(metodo)


def test_id_ignora_rastreamento():
    assert id_noticia("https://www.g1.com/a/?utm_source=rss") == id_noticia("https://g1.com/a")


def test_salvar_monta_upserts():
    rel = noticia("Ri Happy pede RE", "https://estadao.com/x")
    rel.fontes = ["Estadão"]
    m = Match(noticia("Ri Happy pede RE", "https://g1.com/x", "trecho"), ["RJ"], ["recuperação extrajudicial"],
              relacionadas=[rel], paises=["BRA"], resumo_ia="Resumo")
    http = FakeHTTP()
    banco = Supabase("https://abc.supabase.co", "sb_secret_123", enviar=http)
    assert banco.salvar(resultado(m)) == 1

    (met1, cam1, corpo1, h1), (met2, cam2, corpo2, _) = http.chamadas
    assert (met1, cam1) == ("POST", "/noticias?on_conflict=id")
    assert h1["apikey"] == "sb_secret_123" and "Authorization" not in h1
    assert "merge-duplicates" in h1["Prefer"]
    linha = corpo1[0]
    assert linha["fontes"] == ["G1", "Estadão"]
    assert linha["outros_links"] == [{"fonte": "G1", "link": "https://estadao.com/x", "titulo": "Ri Happy pede RE"}]
    assert linha["resumo_ia"] == "Resumo" and linha["trecho_rss"] == "trecho"
    assert linha["publicado_em"] == "2026-10-19T13:00:00+00:00"
    assert "coletado_em" not in linha
    assert cam2 == "/noticias_temas?on_conflict=noticia_id,tema"
    assert corpo2 == [{"noticia_id": linha["id"], "tema": "credito", "filtros": ["RJ"],
                       "palavras": ["recuperação extrajudicial"]}]


def test_chave_jwt_vai_no_authorization():
    http = FakeHTTP()
    Supabase("https://abc.supabase.co", "eyJabc", enviar=http).existentes(["a"])
    assert http.chamadas[0][3]["Authorization"] == "Bearer eyJabc"
    assert http.chamadas[0][1] == "/noticias?select=id,resumo_ia,texto,enviada_em&id=in.(a)"


def test_todas_as_linhas_tem_as_mesmas_colunas():
    a = linha_noticia("a", Match(noticia("A", "https://a"), [], []), "agora")
    b = linha_noticia("b", Match(noticia("B", "https://b", "x"), [], [], resumo_ia="r", paises=["BRA"]), "agora")
    assert a.keys() == b.keys()


def test_reaproveitar():
    m1 = Match(noticia("A", "https://a"), [], [])
    m2 = Match(noticia("B", "https://b"), [], [], resumo_ia="novo")
    existentes = {id_noticia("https://a"): {"resumo_ia": "antigo", "texto": "texto antigo"},
                  id_noticia("https://b"): {"resumo_ia": "antigo", "texto": None}}
    assert reaproveitar(resultado(m1, m2), existentes) == 1
    assert (m1.resumo_ia, m1.noticia.texto) == ("antigo", "texto antigo")
    assert m2.resumo_ia == "novo"


def test_do_ambiente(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert Supabase.do_ambiente() is None
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co/")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    assert Supabase.do_ambiente().base == "https://abc.supabase.co/rest/v1"


HTML = """<html><head><title>Ri Happy</title><meta name="author" content="Maria Souza"></head><body>
<nav>Menu Home Economia Política</nav>
<article><h1>Ri Happy pede recuperação extrajudicial</h1>
<p>A Ri Happy e outras cinco empresas do grupo entraram com um pedido de recuperação extrajudicial
na Justiça de São Paulo, no domingo (18), para renegociar R$ 827,4 milhões em dívidas.</p>
<p>Segundo o pedido, a reestruturação envolve credores financeiros que detêm a maior parte das
debêntures emitidas pela companhia nos últimos anos, com alongamento de prazos e carência.</p>
<p>A empresa afirma que as lojas seguem funcionando normalmente durante o processo.</p>
</article><footer>Copyright Globo</footer></body></html>"""


def test_extrai_texto_principal():
    texto = extrair_texto(HTML)
    assert "R$ 827,4 milhões" in texto and "lojas seguem" in texto
    assert "Menu Home" not in texto and "Copyright" not in texto


def test_baixar_textos_pula_google_e_erros():
    ok = noticia("A", "https://g1.com/a", "curto")
    google = noticia("B", "https://news.google.com/rss/articles/x")
    fora = noticia("C", "https://fora.com/c")

    def baixar(url, timeout):
        if "fora" in url:
            raise TimeoutError()
        assert "google" not in url
        return HTML.encode()

    assert baixar_textos([ok, google, fora], baixar_fn=baixar) == 1
    assert "827,4" in ok.texto and google.texto == "" and fora.texto == ""
    assert ok.autor == "Maria Souza"


def test_url_com_ou_sem_rest_v1():
    for url in ["https://abc.supabase.co", "https://abc.supabase.co/", "https://abc.supabase.co/rest/v1",
                "https://abc.supabase.co/rest/v1/", " https://abc.supabase.co/rest/v1 "]:
        assert Supabase(url, "k").base == "https://abc.supabase.co/rest/v1"



def test_controle_de_envio():
    http = FakeHTTP({"GET": [{"enviado_em": "2026-10-20T09:50:00+00:00"}]})
    banco = Supabase("https://abc.supabase.co", "k", enviar=http)
    assert banco.ultimo_envio() == datetime(2026, 10, 20, 9, 50, tzinfo=timezone.utc)
    assert http.chamadas[0][1] == "/envios?select=enviado_em&order=enviado_em.desc&limit=1"

    banco.registrar_envio(["a", "b"], "Assunto", ["eu@x.com"])
    patch, post = http.chamadas[1], http.chamadas[2]
    assert patch[0] == "PATCH" and patch[1] == "/noticias?id=in.(a,b)" and "enviada_em" in patch[2]
    assert post[0] == "POST" and post[1] == "/envios"
    assert post[2]["noticias"] == 2 and post[2]["destinatarios"] == ["eu@x.com"]


def test_sem_migracao_002_continua_funcionando():
    """Antes de rodar 002_envios.sql: lê sem enviada_em e desliga o controle."""
    chamadas = []

    def enviar(metodo, caminho, corpo, headers):
        chamadas.append((metodo, caminho))
        if "enviada_em" in caminho:
            raise RuntimeError('Supabase 400: {"code":"42703","message":"column noticias.enviada_em does not exist"}')
        if caminho.startswith("/envios"):
            raise RuntimeError('Supabase 404: {"code":"PGRST205","message":"Could not find the table public.envios"}')
        return [{"id": "a", "resumo_ia": "r", "texto": None}]

    banco = Supabase("https://abc.supabase.co", "k", enviar=enviar)
    assert banco.existentes(["a"]) == {"a": {"id": "a", "resumo_ia": "r", "texto": None}}
    assert banco.controle_envio is False
    assert banco.ultimo_envio() is None
    banco.registrar_envio(["a"], "s", ["x"])  # não faz nada
    assert not any(m == "PATCH" for m, _ in chamadas)


def test_mapa_links_inclui_tambem_em():
    linhas = [{"id": "id1", "link": "https://g1.com/a?utm_source=rss",
               "outros_links": [{"fonte": "Valor", "link": "https://valor.com/b"}]}]
    http = FakeHTTP({"GET": linhas})
    mapa = Supabase("https://abc.supabase.co", "k", enviar=http).mapa_links(datetime(2026, 10, 18, tzinfo=timezone.utc))
    assert mapa == {"g1.com/a": "id1", "valor.com/b": "id1"}
    assert http.chamadas[0][1].startswith("/noticias?select=id,link,outros_links,coletado_em&coletado_em=gte.2026-10-18T00:00:00Z")


def test_mesma_historia_com_outro_principal_nao_duplica():
    from n8news.banco import alinhar_com_banco, id_match
    antigo = noticia("Americanas encerra RJ", "https://valor.com/a")
    # Hoje o principal virou a Folha, mas o link do Valor (já gravado) está no grupo
    m1 = Match(noticia("Ações da Americanas disparam", "https://folha.com/b"), ["RJ"], [""], relacionadas=[antigo])
    # Só um link de "também em" bate com o que já está gravado
    m2 = Match(noticia("Azul conclui Chapter 11", "https://g1.com/c"), ["RJ"], [""],
               relacionadas=[noticia("Azul sai do Chapter 11", "https://estadao.com/d")])
    m3 = Match(noticia("Notícia nova", "https://g1.com/nova"), ["RJ"], [""])
    mapa = {"valor.com/a": id_noticia("https://valor.com/a"), "estadao.com/d": "id_azul"}
    assert alinhar_com_banco(resultado(m1, m2, m3), mapa) == 2
    assert m1.noticia.link == "https://valor.com/a" and m1.relacionadas[0].link == "https://folha.com/b"
    assert id_match(m1) == id_noticia("https://valor.com/a")
    assert id_match(m2) == "id_azul" and m2.noticia.link == "https://g1.com/c"
    assert id_match(m3) == id_noticia("https://g1.com/nova")
    http = FakeHTTP()
    Supabase("https://abc.supabase.co", "k", enviar=http).salvar(resultado(m1, m2, m3))
    ids = [l["id"] for l in http.chamadas[0][2]]
    assert ids == [id_noticia("https://valor.com/a"), "id_azul", id_noticia("https://g1.com/nova")]
