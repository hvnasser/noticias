"""Painel de notícias: página estática (GitHub Pages).

Gera `<pasta>/index.html` com os dados embutidos:
- histórico dos últimos `dias` dias, lido do Supabase (se configurado);
- mais as notícias da execução atual (com os resumos mais recentes).

Publica só título, link, fontes, datas, países e resumo (nunca o texto da
matéria). A ordem das seções segue os filtros do temas.yaml.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .banco import id_match
from .busca import chave_link, chave_titulo
from .temas import ResultadoTema, Tema, _parecidas, _radicais, regiao_da_noticia

TITULO = "Notícias"
WORKFLOW = "buscar-noticias.yml"


def _link_atualizar() -> str:
    """Página do GitHub com o botão "Run workflow" (o repositório vem do GitHub Actions)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"https://github.com/{repo}/actions/workflows/{WORKFLOW}" if repo else ""


def _mais_cedo(publicado: str | None, coletado: str | None) -> str | None:
    """Data a mostrar: a publicação, mas nunca depois da 1ª vez que a matéria
    foi coletada (o RSS às vezes "republica" matéria antiga com data nova)."""
    try:
        p = datetime.fromisoformat(publicado.replace("Z", "+00:00")) if publicado else None
        c = datetime.fromisoformat(coletado.replace("Z", "+00:00")) if coletado else None
    except ValueError:
        return publicado
    if p and c and p.tzinfo and c.tzinfo and c < p:
        return coletado
    return publicado


def _item(tema_id, filtros, titulo, link, fontes, outros, paises, publicado, coletado, resumo) -> dict:
    publicado = _mais_cedo(publicado, coletado)
    return {
        "tema": tema_id,
        "filtro": filtros[0] if filtros else "",
        "regiao": regiao_da_noticia(titulo, paises or []),
        "titulo": titulo,
        "link": link,
        "fontes": fontes or [],
        "outros": outros or [],
        "paises": paises or [],
        "publicado": publicado,
        "coletado": coletado,
        "resumo": "" if (resumo or "").strip() == titulo.strip() else (resumo or ""),
    }


def montar_dados(temas: list[Tema], atuais: list[ResultadoTema], historico: list[dict],
                 agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    itens: dict[tuple[str, str], dict] = {}

    for linha in historico:
        n = linha.get("noticias") or {}
        if not n.get("titulo"):
            continue
        outros = [{"fonte": o.get("fonte", ""), "link": o.get("link", "")} for o in n.get("outros_links") or []]
        itens[(n["id"], linha["tema"])] = _item(
            linha["tema"], linha.get("filtros") or [], n["titulo"], n["link"], n.get("fontes"), outros,
            n.get("paises"), n.get("publicado_em"), n.get("coletado_em") or linha.get("criado_em"),
            n.get("resumo_ia"))

    # A execução atual tem prioridade (resumo e classificação mais recentes)
    for res in atuais:
        for m in res.noticias:
            n = m.noticia
            chave = (id_match(m), res.tema.id)
            fontes = list(dict.fromkeys(n.fontes + [f for r in m.relacionadas for f in r.fontes]))
            outros = [{"fonte": r.fonte, "link": r.link} for r in m.relacionadas]
            coletado = itens.get(chave, {}).get("coletado") or agora.isoformat()
            item = _item(res.tema.id, m.filtros, n.titulo, n.link, fontes, outros, m.paises,
                         n.publicado_em.isoformat() if n.publicado_em else None, coletado, m.resumo_ia)
            item["regiao"] = m.regiao
            itens[chave] = item

    for chave, item in itens.items():
        item["id"] = f"{chave[0]}:{chave[1]}"

    lista = []
    for t in temas:
        lista += agrupar_assuntos([i for i in itens.values() if i["tema"] == t.id], t)
    lista += [i for i in itens.values() if i["tema"] not in {t.id for t in temas}]

    return {
        "atualizado": agora.isoformat(),
        "temas": [
            {"id": t.id, "nome": t.nome,
             "filtros": [{"nome": f.nome, "separar": bool(f.separar_brasil and not f.regiao)} for f in t.filtros]}
            for t in temas
        ],
        "noticias": sorted(lista, key=lambda i: i["publicado"] or i["coletado"] or "", reverse=True),
    }


# ---------- Uma notícia por assunto ----------
# O histórico junta várias execuções: a mesma história aparece com títulos
# diferentes ("Americanas encerra RJ", "Ações da Americanas disparam...").
# No painel, cada assunto vira um cartão só (a mais recente), com as outras
# matérias em "Mais N matérias sobre o assunto".

JANELA_ASSUNTO = timedelta(days=3)

# Palavras com maiúscula que não identificam um assunto (início de frase,
# instituições, lugares). Comparadas pelos 5 primeiros caracteres.
_NOMES_COMUNS = _radicais("""
acoes acao justica governo mercado mercados investidores investidor credores credor empresa empresas
banco bancos bolsa bolsas juros dolar ibovespa brasil brasileira brasileiro eua china europa tribunal
stj stf tjsp cvm fed tesouro titulos divida dividas risco credito debentures fundo fundos gestora
gestoras varejista varejistas construtora operadora setor ministro ministerio presidente governo
como por que quem qual quando onde apos com sem mais menos nova novo novas novos onda alta altas
queda quedas hora entenda veja saiba analise opiniao exclusivo exclusiva caso grupo grupos rio sao
paulo janeiro minas sul norte the how why what when new first firstft live update updates week
""")
_NOMES_COMUNS |= {"mp", "bc", "sp", "rj", "re", "us", "uk", "eu", "ai", "ia"}


def _quando(i: dict) -> datetime | None:
    try:
        d = datetime.fromisoformat((i.get("publicado") or i.get("coletado") or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _palavras_maiusculas(titulo: str) -> list[str]:
    return [w for w in re.split(r"[^\w$]+", titulo) if w and (w[0].isupper() or w[0].isdigit())]


def _titulo_capitalizado(titulo: str) -> bool:
    """Title Case (comum em inglês): quase toda palavra começa com maiúscula."""
    longas = [w for w in re.split(r"\W+", titulo) if len(w) >= 4]
    return len(longas) >= 3 and sum(w[0].isupper() for w in longas) / len(longas) >= 0.6


def _nomes(titulo: str, comuns: set[str], aceitos: set[str] | None) -> set[str]:
    """Nomes que identificam o assunto (empresas, países...).
    Em títulos Title Case, só valem nomes vistos também em títulos normais."""
    nomes = {r for w in _palavras_maiusculas(titulo) if not w.isdigit() for r in _radicais(w)} - comuns
    return nomes & aceitos if aceitos is not None else nomes


def _mesmo_assunto(a: dict, b: dict, genericas: set[str]) -> bool:
    if a["_links"] & b["_links"] or chave_titulo(a["titulo"]) == chave_titulo(b["titulo"]):
        return True
    if _parecidas((a["_rad"], a["_nr"]), (b["_rad"], b["_nr"]), genericas):
        return True
    comum = a["_rad"] & b["_rad"]
    if len(comum - genericas) >= 4 and len(comum) / min(len(a["_rad"]), len(b["_rad"])) >= 0.7:
        return True  # títulos quase iguais, mesmo sem nome próprio em comum
    # Mesma seção, mesmo nome (ex: Americanas) e datas próximas
    # (o nome pode estar em minúscula no outro título: "dívida argentina")
    if a["filtro"] != b["filtro"] or not ((a["_nomes"] & b["_rad"]) | (b["_nomes"] & a["_rad"])):
        return False
    qa, qb = a["_q"], b["_q"]
    return qa is not None and qb is not None and abs(qa - qb) <= JANELA_ASSUNTO


def agrupar_assuntos(itens: list[dict], tema: Tema) -> list[dict]:
    """Junta as notícias do mesmo assunto num cartão só (a mais recente)."""
    if len(itens) < 2:
        return itens
    genericas = tema.radicais_genericos()
    comuns = _NOMES_COMUNS | genericas
    # Nomes muito frequentes no tema (ex: "Brasil") não identificam assunto
    contagem: dict[str, int] = {}
    for i in itens:
        for r in _nomes(i["titulo"], comuns, None):
            contagem[r] = contagem.get(r, 0) + 1
    limite = max(6, len(itens) // 6)
    comuns |= {r for r, n in contagem.items() if n > limite}
    vistos = set()
    for i in itens:
        if not _titulo_capitalizado(i["titulo"]):
            vistos |= _nomes(i["titulo"], comuns, None)
    for i in itens:
        i["_links"] = {chave_link(i["link"])} | {chave_link(o["link"]) for o in i.get("outros") or [] if o.get("link")}
        i["_rad"] = _radicais(i["titulo"])
        i["_nr"] = _radicais(" ".join(_palavras_maiusculas(i["titulo"])))
        i["_nomes"] = _nomes(i["titulo"], comuns, vistos if _titulo_capitalizado(i["titulo"]) else None)
        i["_q"] = _quando(i)

    # Une os parecidos (um grupo pode encadear várias matérias)
    pai = list(range(len(itens)))

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for x in range(len(itens)):
        for y in range(x + 1, len(itens)):
            if raiz(x) != raiz(y) and _mesmo_assunto(itens[x], itens[y], genericas):
                pai[raiz(y)] = raiz(x)

    grupos: dict[int, list[dict]] = {}
    for x, i in enumerate(itens):
        grupos.setdefault(raiz(x), []).append(i)

    ordem = {f.nome: n for n, f in enumerate(tema.filtros)}
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    resultado = []
    for membros in grupos.values():
        membros.sort(key=lambda i: i["_q"] or minimo, reverse=True)
        principal = dict(membros[0])
        # Seção: a primeira na ordem do temas.yaml (ex: RJ antes de Spreads)
        principal["filtro"] = min((i["filtro"] for i in membros), key=lambda f: ordem.get(f, len(ordem)))
        principal["coletado"] = max((i["coletado"] or "" for i in membros), default="") or None
        # "Também em": outras fontes do mesmo fato, sem repetir jornal
        vistas, outros = set(principal["fontes"]), []
        for o in [o for i in membros for o in (i.get("outros") or [])]:
            if o.get("fonte") and o["fonte"] not in vistas:
                vistas.add(o["fonte"])
                outros.append(o)
        principal["outros"] = outros
        principal["mais"] = [{"titulo": i["titulo"], "link": i["link"], "fontes": i["fontes"],
                              "publicado": i["publicado"], "resumo": i["resumo"]} for i in membros[1:]]
        resultado.append(principal)
    for i in itens + resultado:
        for k in ("_links", "_rad", "_nr", "_nomes", "_q"):
            i.pop(k, None)
    return resultado


def gerar_painel(pasta: str | Path, temas: list[Tema], atuais: list[ResultadoTema],
                 banco=None, dias: int = 30) -> tuple[Path, int, list[str]]:
    """Escreve <pasta>/index.html. Devolve (arquivo, nº de notícias, avisos)."""
    avisos: list[str] = []
    historico: list[dict] = []
    if banco:
        try:
            historico = banco.historico(datetime.now(timezone.utc) - timedelta(days=dias))
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Painel: não consegui ler o histórico do Supabase ({e}); só notícias de hoje")
    dados = montar_dados(temas, atuais, historico)
    dados["atualizar"] = _link_atualizar()
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    # "</" escapado para o JSON não fechar a tag <script> antes da hora
    js = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    arquivo = pasta / "index.html"
    arquivo.write_text(MODELO.replace("__TITULO__", TITULO).replace("__DADOS__", js), encoding="utf-8")
    (pasta / ".nojekyll").write_text("")
    return arquivo, len(dados["noticias"]), avisos


MODELO = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>__TITULO__</title>
<style>
:root {
  --fundo: #f6f7f9; --cartao: #ffffff; --texto: #1f2328; --suave: #59636e; --borda: #d8dee4;
  --link: #0a58ca; --destaque: #e7f0ff; --novo: #1a7f37; --novo-fundo: #dafbe1; --chip: #eef1f4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fundo: #0d1117; --cartao: #161b22; --texto: #e6edf3; --suave: #8d96a0; --borda: #30363d;
    --link: #58a6ff; --destaque: #1f2d44; --novo: #3fb950; --novo-fundo: #12261a; --chip: #21262d;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--fundo); color: var(--texto);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header { position: sticky; top: 0; z-index: 5; background: var(--cartao); border-bottom: 1px solid var(--borda); }
.topo { max-width: 920px; margin: 0 auto; padding: 14px 16px 10px; }
h1 { font-size: 20px; margin: 0; }
.sub { color: var(--suave); font-size: 13px; }
.controles { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.controles input[type=search] { flex: 1 1 220px; min-width: 0; padding: 7px 10px; border: 1px solid var(--borda);
  border-radius: 8px; background: var(--fundo); color: var(--texto); font: inherit; }
select { padding: 7px 8px; border: 1px solid var(--borda); border-radius: 8px; background: var(--fundo);
  color: var(--texto); font: inherit; }
.grupo-botoes { display: inline-flex; border: 1px solid var(--borda); border-radius: 8px; overflow: hidden; }
.grupo-botoes button { border: 0; background: var(--fundo); color: var(--texto); padding: 7px 10px; font: inherit; cursor: pointer; }
.grupo-botoes button + button { border-left: 1px solid var(--borda); }
.grupo-botoes button[aria-pressed=true] { background: var(--destaque); color: var(--link); font-weight: 600; }
label.check { display: inline-flex; align-items: center; gap: 6px; color: var(--suave); font-size: 14px; }
.secoes { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.secoes a { font-size: 12px; text-decoration: none; color: var(--texto); background: var(--chip);
  padding: 3px 9px; border-radius: 999px; white-space: nowrap; }
main { max-width: 920px; margin: 0 auto; padding: 8px 16px 48px; }
h2 { font-size: 18px; margin: 28px 0 4px; }
h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--suave);
  margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--borda); scroll-margin-top: 150px; }
h4 { font-size: 13px; margin: 12px 0 6px; }
.conta { color: var(--suave); font-weight: normal; }
.item { background: var(--cartao); border: 1px solid var(--borda); border-radius: 10px; padding: 10px 12px; margin: 0 0 8px; }
.item a.t { color: var(--link); font-weight: 600; text-decoration: none; }
.item a.t:hover { text-decoration: underline; }
.meta { color: var(--suave); font-size: 12px; margin-top: 2px; }
.meta a { color: var(--suave); }
.resumo { margin-top: 5px; font-size: 14px; }
.novo { display: inline-block; font-size: 11px; font-weight: 700; color: var(--novo); background: var(--novo-fundo);
  border-radius: 4px; padding: 0 5px; margin-right: 6px; vertical-align: 1px; }
.mais { margin-top: 6px; font-size: 13px; }
.mais summary { color: var(--suave); cursor: pointer; }
.mais ul { margin: 6px 0 0; padding-left: 18px; }
.mais li { margin: 3px 0; }
.mais li a { color: var(--link); text-decoration: none; }
.mais li span { color: var(--suave); font-size: 12px; }
.cabeca { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
a.botao { border: 1px solid var(--borda); background: var(--fundo); color: var(--texto); border-radius: 8px;
  padding: 4px 10px; font-size: 13px; text-decoration: none; white-space: nowrap; }
a.botao:hover { background: var(--destaque); color: var(--link); }
.vazio { color: var(--suave); text-align: center; padding: 40px 0; }
footer { color: var(--suave); font-size: 12px; text-align: center; padding: 20px; }
</style>
</head>
<body>
<header><div class="topo">
  <div class="cabeca">
    <div><h1>__TITULO__</h1><div class="sub" id="sub"></div></div>
    <a class="botao" id="atualizar" target="_blank" rel="noopener" hidden
       title="Abre o GitHub: clique em Run workflow. O painel atualiza em 2 a 3 minutos.">Atualizar agora ↗</a>
  </div>
  <div class="controles">
    <input type="search" id="busca" placeholder="Buscar (ex: Ri Happy, debêntures, França)" aria-label="Buscar">
    <select id="periodo" aria-label="Período">
      <option value="24">Últimas 24h</option>
      <option value="48">Últimas 48h</option>
      <option value="168">Últimos 7 dias</option>
      <option value="720">Últimos 30 dias</option>
    </select>
    <select id="tema" aria-label="Tema"></select>
    <span class="grupo-botoes" role="group" aria-label="Região">
      <button type="button" data-regiao="" aria-pressed="true">Todas</button>
      <button type="button" data-regiao="brasil" aria-pressed="false">Brasil</button>
      <button type="button" data-regiao="mundo" aria-pressed="false">Mundo</button>
    </span>
    <label class="check"><input type="checkbox" id="sonovas"> Só novas desde a última visita</label>
  </div>
  <nav class="secoes" id="secoes"></nav>
</div></header>
<main id="conteudo"></main>
<footer>Títulos, resumos e links das fontes originais. Atualizado automaticamente.</footer>
<script id="dados" type="application/json">__DADOS__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById("dados").textContent);
  var FUSO = "America/Sao_Paulo";
  var fmt = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  var fmtLongo = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, dateStyle: "short", timeStyle: "short" });

  // Última visita (só neste navegador)
  var CHAVE = "noticias.ultimaVisita", ultimaVisita = null;
  try { ultimaVisita = localStorage.getItem(CHAVE); localStorage.setItem(CHAVE, new Date().toISOString()); } catch (e) {}
  var corteNovas = ultimaVisita ? new Date(ultimaVisita) : null;

  var estado = { busca: "", horas: 24, tema: "", regiao: "", soNovas: false };
  var el = function (id) { return document.getElementById(id); };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function semAcento(s) { return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function quando(i) { return new Date(i.publicado || i.coletado); }
  function ehNova(i) { return corteNovas && i.coletado && new Date(i.coletado) > corteNovas; }
  function ancora(s) { return "s-" + semAcento(s).replace(/[^a-z0-9]+/g, "-"); }

  // Seletor de tema (só aparece se houver mais de um)
  var selTema = el("tema");
  selTema.innerHTML = '<option value="">Todos os temas</option>' +
    D.temas.map(function (t) { return '<option value="' + esc(t.id) + '">' + esc(t.nome) + "</option>"; }).join("");
  if (D.temas.length < 2) selTema.style.display = "none";

  el("sub").textContent = "Atualizado em " + fmtLongo.format(new Date(D.atualizado)) +
    (ultimaVisita ? " · sua última visita: " + fmtLongo.format(new Date(ultimaVisita)) : "");

  function filtradas() {
    var agora = Date.now(), termos = semAcento(estado.busca).split(/\s+/).filter(Boolean);
    return D.noticias.filter(function (i) {
      if (estado.tema && i.tema !== estado.tema) return false;
      if (estado.regiao && i.regiao !== estado.regiao) return false;
      if (agora - quando(i).getTime() > estado.horas * 3600e3) return false;
      if (estado.soNovas && !ehNova(i)) return false;
      if (termos.length) {
        var alvo = semAcento([i.titulo, i.resumo, i.fontes.join(" ")].concat(
          (i.mais || []).map(function (x) { return x.titulo + " " + x.fontes.join(" "); })).join(" "));
        for (var k = 0; k < termos.length; k++) if (alvo.indexOf(termos[k]) < 0) return false;
      }
      return true;
    });
  }

  function cartao(i) {
    var resumo = esc(i.resumo);
    var m = /^(Opinião[^:]*):\s*([\s\S]*)$/.exec(i.resumo || "");
    if (m) resumo = "<b>" + esc(m[1]) + ":</b> " + esc(m[2]);
    var outros = (i.outros || []).map(function (o) { return '<a href="' + esc(o.link) + '" target="_blank" rel="noopener">' + esc(o.fonte) + "</a>"; });
    return '<article class="item">' +
      (ehNova(i) ? '<span class="novo">NOVA</span>' : "") +
      '<a class="t" href="' + esc(i.link) + '" target="_blank" rel="noopener">' + esc(i.titulo) + "</a>" +
      '<div class="meta">' + esc(i.fontes.join(", ")) + " · " + esc(fmt.format(quando(i))) +
      (outros.length ? " · também em " + outros.join(", ") : "") + "</div>" +
      (resumo ? '<div class="resumo">' + resumo + "</div>" : "") + maisMaterias(i) + "</article>";
  }

  function maisMaterias(i) {
    var mais = i.mais || [];
    if (!mais.length) return "";
    return '<details class="mais"><summary>Mais ' + mais.length + (mais.length === 1 ? " matéria" : " matérias") +
      " sobre o assunto</summary><ul>" + mais.map(function (x) {
        return '<li><a href="' + esc(x.link) + '" target="_blank" rel="noopener">' + esc(x.titulo) + "</a> <span>" +
          esc(x.fontes.join(", ")) + (x.publicado ? " · " + esc(fmt.format(new Date(x.publicado))) : "") + "</span></li>";
      }).join("") + "</ul></details>";
  }

  function render() {
    var lista = filtradas(), html = [], nav = [];
    D.temas.forEach(function (t) {
      if (estado.tema && t.id !== estado.tema) return;
      var doTema = lista.filter(function (i) { return i.tema === t.id; });
      if (!doTema.length) return;
      if (D.temas.length > 1) html.push("<h2>" + esc(t.nome) + ' <span class="conta">(' + doTema.length + ")</span></h2>");
      var conhecidos = t.filtros.map(function (f) { return f.nome; });
      var filtros = t.filtros.concat([{ nome: "Outras", separar: true }]);
      filtros.forEach(function (f) {
        var doFiltro = doTema.filter(function (i) {
          return f.nome === "Outras" ? conhecidos.indexOf(i.filtro) < 0 : i.filtro === f.nome; });
        if (!doFiltro.length) return;
        var id = ancora(t.id + " " + f.nome);
        nav.push('<a href="#' + id + '">' + esc(f.nome) + " (" + doFiltro.length + ")</a>");
        html.push('<h3 id="' + id + '">' + esc(f.nome) + ' <span class="conta">(' + doFiltro.length + ")</span></h3>");
        var partes = f.separar
          ? [["Brasil", doFiltro.filter(function (i) { return i.regiao === "brasil"; })],
             ["Mundo", doFiltro.filter(function (i) { return i.regiao !== "brasil"; })]]
          : [["", doFiltro]];
        partes.forEach(function (p) {
          if (!p[1].length) return;
          if (p[0]) html.push("<h4>" + p[0] + ' <span class="conta">(' + p[1].length + ")</span></h4>");
          p[1].sort(function (a, b) { return quando(b) - quando(a); }).forEach(function (i) { html.push(cartao(i)); });
        });
      });
    });
    el("conteudo").innerHTML = html.length ? html.join("") :
      '<p class="vazio">Nenhuma notícia com esses filtros. Tente um período maior.</p>';
    el("secoes").innerHTML = nav.join("");
  }

  el("busca").addEventListener("input", function (e) { estado.busca = e.target.value; render(); });
  el("periodo").addEventListener("change", function (e) { estado.horas = +e.target.value; render(); });
  selTema.addEventListener("change", function (e) { estado.tema = e.target.value; render(); });
  el("sonovas").addEventListener("change", function (e) { estado.soNovas = e.target.checked; render(); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-regiao]"), function (b) {
    b.addEventListener("click", function () {
      estado.regiao = b.getAttribute("data-regiao");
      Array.prototype.forEach.call(document.querySelectorAll("[data-regiao]"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      render();
    });
  });
  if (!corteNovas) el("sonovas").parentNode.style.display = "none";
  if (D.atualizar) { el("atualizar").href = D.atualizar; el("atualizar").hidden = false; }
  render();
})();
</script>
</body>
</html>
"""
