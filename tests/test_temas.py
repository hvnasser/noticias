import pytest

from n8news.busca import Noticia
from n8news.temas import Filtro, Tema, carregar_temas, classificar, normalizar


def noticia(titulo, resumo="", categoria="economia"):
    return Noticia(titulo=titulo, link="https://x/" + titulo, resumo=resumo, fonte="X",
                   categoria=categoria, publicado_em=None, fontes=["X"], categorias=[categoria])


@pytest.fixture(scope="module")
def credito():
    return next(t for t in carregar_temas("temas.yaml") if t.id == "credito")


_PAISES = None


def filtros_que_casam(tema, titulo, resumo="", categoria="economia"):
    global _PAISES
    if _PAISES is None:
        from n8news.paises import carregar_paises
        _PAISES = carregar_paises("paises.yaml")
    res = classificar([noticia(titulo, resumo, categoria)], [tema], _PAISES)[0]
    return res.noticias[0].filtros if res.noticias else []


@pytest.mark.parametrize("titulo, filtro", [
    ("Spreads de debêntures voltam a cair com forte demanda por crédito privado", "Spreads corporativos"),
    ("High-yield bond spreads widen as investors flee risk", "Spreads corporativos"),
    ("Private credit funds face redemptions", "Spreads corporativos"),
    ("Risco-país do Brasil cai ao menor nível do ano", "Spreads soberanos e risco-país"),
    ("CDS de 5 anos do Brasil recua", "Spreads soberanos e risco-país"),
    ("French OAT-Bund spread hits widest since 2012", "Spreads soberanos e risco-país"),
    ("Gilts sell off as UK borrowing costs jump", "Spreads soberanos e risco-país"),
    ("Moody's rebaixa nota de crédito da Colômbia", "Ratings"),
    ("Fitch downgrades France's credit rating to A+", "Ratings"),
    ("Pedidos de recuperação judicial no agro caem 20,7%, diz Serasa", "Recuperação judicial e extrajudicial"),
    ("Empresa X entra com pedido de recuperação judicial", "Recuperação judicial e extrajudicial"),
    ("Retailer files for Chapter 11 bankruptcy protection", "Default e calote"),
    ("Argentina deixou de pagar cupom de bônus", "Default e calote"),
    ("O que está por trás do pedido de falência da Character.AI?", "Default e calote"),
    ("Investors are pushing up the cost of insurance against an Oracle default", "Default e calote"),
    ("Companhia aérea fecha renegociação de dívidas com credores", "Renegociação e reestruturação de dívida"),
    ("Thames Water reaches debt restructuring deal with bondholders", "Renegociação e reestruturação de dívida"),
    ("Localiza aprova emissão de R$ 1 bilhão em debêntures", "Captações de empresas brasileiras"),
    ("Ofertas de debêntures superam R$ 3,6 bilhões na última semana", "Captações de empresas brasileiras"),
    ("Petrobras prices $2 billion bond offering", "Captações de empresas brasileiras"),
    ("Suzano capta US$ 1 bi com green bond no exterior", "Captações de empresas brasileiras"),
])
def test_entra_no_tema(credito, titulo, filtro):
    assert filtro in filtros_que_casam(credito, titulo)


@pytest.mark.parametrize("titulo, categoria", [
    ("Virus spread accelerates in Europe", "mundo"),
    ("Bundestag approves new budget", "mundo"),
    ("iPhone gets a major upgrade", "economia"),
    ("Time é rebaixado para a série B", "brasil"),
    ("Serasa faz feirão para renegociar dívidas", "economia"),
    ("S&P 500 fecha em alta com balanços", "mercado"),
    ("Petrobras anuncia dividendos", "economia"),
    ("Empresa entra em recuperação judicial", "tecnologia"),  # categoria fora do tema
    ("CDS-PP apresenta voto de pesar", "mundo"),
    ("Olhar de frente para um ovo é arriscar a falência do entendimento", "brasil"),
    ("Mercado Livre chega a três mega CDs no Rio Grande do Sul", "credito"),
    ("Best high-yield savings interest rates today: Earn up to 4.3% APY", "credito"),
    ("5 High-Yield Stocks to Buy Now Before Your Retirement Starts", "credito"),
    ("EchoStar gains Speculative Buy rating on $20B deal", "credito"),
    ("Oil prices fall as traders weigh Russia sanctions waiver", "economia"),
    ("Petrobras anuncia emissão de novas ações", "economia"),
])
def test_nao_entra_no_tema(credito, titulo, categoria):
    assert filtros_que_casam(credito, titulo, categoria=categoria) == []


def test_regras_das_palavras():
    assert normalizar("Risco-País") == " risco pais "
    f = Filtro("t", palavras=["renegoci*", "high yield", "default"])
    assert f.casa(normalizar("Renegociação aprovada"))
    assert f.casa(normalizar("high-yield bonds"))
    assert not f.casa(normalizar("company defaulted"))  # palavra inteira


def test_exige_e_filtro_desligado(tmp_path):
    f = Filtro("t", palavras=["spread"], exige=["bond"])
    assert not f.casa(normalizar("spread of the virus"))
    assert f.casa(normalizar("bond spread widens"))

    arq = tmp_path / "t.yaml"
    arq.write_text("""
temas:
  - id: a
    nome: A
    filtros:
      - { nome: ligado, palavras: [default] }
      - { nome: desligado, palavras: [calote], ativo: false }
  - id: b
    nome: B
    ativo: false
    filtros: [{ nome: x, palavras: [x] }]
""")
    temas = carregar_temas(arq)
    assert [t.id for t in temas] == ["a"]
    assert [f.nome for f in temas[0].filtros] == ["ligado"]


def test_excluidas_ficam_registradas():
    tema = Tema("c", "C", [Filtro("d", ["dívida*"])], excluir=["Serasa"])
    res = classificar([noticia("Serasa ajuda a renegociar dívidas")], [tema])[0]
    assert res.noticias == [] and res.excluidas[0][1] == "Serasa"


def test_agrupa_mesma_noticia_de_jornais_diferentes(credito):
    titulos = [
        ("Ri Happy solicita recuperação extrajudicial com dívida de R$ 827,4 milhões", "Folha de Curitiba"),
        ("Grupo Ri Happy entra com pedido de recuperação extrajudicial", "Estadão"),
        ("Ri Happy pede recuperação extrajudicial 48 dias após mudar de dono", "Exame"),
        ("Empresa Alfa entra com pedido de recuperação judicial", "Valor"),
        ("Beta entra com pedido de recuperação judicial", "Valor"),
        ("BoE scrutinises Barclays over claim traders misrepresented private credit exposure", "FT"),
        ("Bank of England probes Barclays over private credit loans - report", "Reuters"),
        ("BoE probes Barclays’ handling of private credit risks, FT reports", "Yahoo"),
        ("Localiza aprova emissão de R$ 1 bilhão em debêntures", "UOL"),
        ("Localiza (RENT3) capta R$ 1 bi em debêntures com vencimento em 24 meses", "Visno"),
        ("Valid aprova emissão de R$ 200 milhões em debêntures, com prazo de cinco anos", "ADVFN"),
    ]
    noticias = []
    for t, fonte in titulos:
        n = noticia(t)
        n.fontes = [fonte]
        noticias.append(n)
    noticias[1].resumo = "O grupo pediu..."  # a com resumo vira a principal
    res = classificar(noticias, [credito])[0]
    grupos = sorted(sorted([m.noticia.fontes[0]] + [r.fontes[0] for r in m.relacionadas]) for m in res.noticias)
    assert grupos == sorted([
        ["Estadão", "Exame", "Folha de Curitiba"],
        ["Valor"], ["Valor"],
        ["FT", "Reuters", "Yahoo"],
        ["UOL", "Visno"],
        ["ADVFN"],
    ])
    ri = next(m for m in res.noticias if "Happy" in m.noticia.titulo)
    assert ri.noticia.fontes == ["Estadão"]



@pytest.mark.parametrize("titulo, nao_esta_em", [
    ("Oracle prices $18 billion bond sale", "Captações de empresas brasileiras"),  # empresa estrangeira
    ("Debêntures incentivadas ignoram rali do mercado", "Captações de empresas brasileiras"),  # mercado, não captação
])
def test_captacoes_so_brasil_e_so_emissao(credito, titulo, nao_esta_em):
    assert nao_esta_em not in filtros_que_casam(credito, titulo)


def test_regiao_da_noticia():
    from n8news.temas import regiao_da_noticia
    assert regiao_da_noticia("Debêntures incentivadas ignoram rali do mercado", []) == "brasil"
    assert regiao_da_noticia("What has actually battered the bond market?", []) == "mundo"
    assert regiao_da_noticia("Risco-país da Argentina supera 700 pontos", ["ARG"]) == "mundo"
    assert regiao_da_noticia("Petrobras prices bond", ["BRA"]) == "brasil"


def test_prioridade_dos_jornais_no_link_principal():
    from datetime import datetime, timezone
    from n8news.busca import Noticia
    from n8news.temas import Match, agrupar_parecidas, carregar_prioridade
    prio = carregar_prioridade("temas.yaml")

    def m(fonte, link, regiao, titulo="Americanas encerra recuperação judicial após 3 anos"):
        n = Noticia(titulo, link, "trecho", fonte, "economia", datetime(2026, 10, 29, tzinfo=timezone.utc), [fonte], ["economia"])
        return Match(n, ["RJ"], [""], regiao=regiao)

    # Brasil: Valor (mesmo pelo Google News) > Estadão > O Globo > Folha > outros
    grupo = [m("G1", "https://g1.com/a", "brasil"), m("Folha Mercado", "https://folha.com/a", "brasil"),
             m("Estadão MT", "https://estadao.com/a", "brasil"),
             m("Valor Econômico", "https://news.google.com/rss/articles/x", "brasil")]
    assert agrupar_parecidas(grupo, prioridade=prio)[0].noticia.fonte == "Valor Econômico"
    grupo = [m("CNN Brasil", "https://cnn.com/a", "brasil"), m("O GLOBO", "https://oglobo.com/a", "brasil"),
             m("Folha", "https://folha.com/a", "brasil")]
    assert agrupar_parecidas(grupo, prioridade=prio)[0].noticia.fonte == "O GLOBO"
    # G1 conta como Globo: na frente da Folha
    grupo = [m("Folha", "https://folha.com/a", "brasil"), m("G1 Economia", "https://g1.com/a", "brasil"),
             m("CNN Brasil", "https://cnn.com/a", "brasil")]
    assert agrupar_parecidas(grupo, prioridade=prio)[0].noticia.fonte == "G1 Economia"

    # Mundo: WSJ > FT > Reuters > Bloomberg > outros
    t = "Treasury yields fall after Fed holds rates steady at 4.5%"
    grupo = [m("Bloomberg.com", "https://bloomberg.com/a", "mundo", t), m("Reuters", "https://reuters.com/a", "mundo", t),
             m("Financial Times", "https://ft.com/a", "mundo", t), m("CNBC", "https://cnbc.com/a", "mundo", t)]
    assert agrupar_parecidas(grupo, prioridade=prio)[0].noticia.fonte == "Financial Times"
    grupo.append(m("Wall Street Journal", "https://news.google.com/rss/articles/y", "mundo", t))
    principal = agrupar_parecidas(grupo, prioridade=prio)[0]
    assert principal.noticia.fonte == "Wall Street Journal" and len(principal.relacionadas) == 4

    # Ninguém da lista: mantém o critério antigo (link direto, mais fontes)
    grupo = [m("CNN Brasil", "https://news.google.com/rss/articles/z", "brasil"), m("InfoMoney", "https://infomoney.com/b", "brasil")]
    assert agrupar_parecidas(grupo, prioridade=prio)[0].noticia.fonte == "InfoMoney"
