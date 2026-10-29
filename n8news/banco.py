"""Grava notícias e resumos no Supabase (API REST/PostgREST).

Precisa das variáveis SUPABASE_URL e SUPABASE_KEY (chave secreta / service
role - nunca a chave pública). Tabelas: supabase/schema.sql.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .busca import chave_link, chave_titulo
from .temas import Match, ResultadoTema

LOTE = 50


def id_noticia(link: str) -> str:
    return hashlib.sha1(chave_link(link).encode()).hexdigest()


class Supabase:
    def __init__(self, url: str, chave: str, timeout: float = 30, enviar=None):
        # Aceita tanto "https://x.supabase.co" quanto "https://x.supabase.co/rest/v1"
        url = url.strip().rstrip("/")
        url = url.removesuffix("/rest/v1").rstrip("/")
        self.base = url + "/rest/v1"
        self.chave = chave
        self.timeout = timeout
        self._enviar = enviar or self._enviar_http
        # Controle de envio (coluna enviada_em e tabela envios). Fica False se a
        # migração supabase/migracoes/002_envios.sql ainda não foi rodada.
        self.controle_envio = True

    @classmethod
    def do_ambiente(cls) -> "Supabase | None":
        url, chave = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_KEY", "").strip()
        return cls(url, chave) if url and chave else None

    # ------------------------------------------------------------ HTTP
    def _headers(self, extra: dict | None = None) -> dict:
        h = {"apikey": self.chave, "Content-Type": "application/json"}
        if self.chave.startswith("eyJ"):  # chave antiga (JWT) também vai no Authorization
            h["Authorization"] = f"Bearer {self.chave}"
        return {**h, **(extra or {})}

    def _enviar_http(self, metodo: str, caminho: str, corpo, headers: dict):
        dados = json.dumps(corpo, ensure_ascii=False).encode() if corpo is not None else None
        req = urllib.request.Request(self.base + caminho, data=dados, method=metodo, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                conteudo = resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Supabase {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        return json.loads(conteudo) if conteudo else None

    # ------------------------------------------------------------ leitura
    def existentes(self, ids: list[str]) -> dict[str, dict]:
        """Notícias já gravadas (para reaproveitar resumo e texto)."""
        achadas = {}
        for i in range(0, len(ids), LOTE):
            lote = ids[i : i + LOTE]
            filtro = f"&id=in.({','.join(lote)})"
            linhas = None
            if self.controle_envio:
                try:
                    linhas = self._enviar("GET", "/noticias?select=id,resumo_ia,texto,enviada_em" + filtro,
                                          None, self._headers())
                except RuntimeError as e:
                    if "enviada_em" not in str(e):
                        raise
                    self.controle_envio = False  # migração 002 pendente
            if linhas is None:
                linhas = self._enviar("GET", "/noticias?select=id,resumo_ia,texto" + filtro, None, self._headers())
            for linha in linhas or []:
                achadas[linha["id"]] = linha
        return achadas

    def _paginar(self, base: str) -> list[dict]:
        linhas, pagina = [], 1000
        while True:
            lote = self._enviar("GET", f"{base}&limit={pagina}&offset={len(linhas)}", None, self._headers()) or []
            linhas += lote
            if len(lote) < pagina:
                return linhas

    def recentes(self, desde: datetime) -> list[dict]:
        """Notícias gravadas desde a data: id, link, título, "também em" e 1ª coleta."""
        data = desde.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return self._paginar(f"/noticias?select=id,link,titulo,outros_links,coletado_em"
                             f"&coletado_em=gte.{data}&order=coletado_em.asc")

    def mapa_links(self, desde: datetime) -> dict[str, str]:
        return mapa_links(self.recentes(desde))

    def historico(self, desde: datetime) -> list[dict]:
        """Notícias classificadas em temas desde a data (para o painel).
        Uma linha por notícia+tema, com os dados da notícia embutidos."""
        campos = "id,titulo,link,fontes,outros_links,paises,publicado_em,coletado_em,resumo_ia"
        base = (f"/noticias_temas?select=tema,filtros,criado_em,noticias({campos})"
                f"&criado_em=gte.{desde.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
                f"&order=criado_em.desc")
        return self._paginar(base)

    def ultimo_envio(self) -> datetime | None:
        """Data do último e-mail enviado (None se nunca enviou ou sem controle)."""
        if not self.controle_envio:
            return None
        try:
            linhas = self._enviar("GET", "/envios?select=enviado_em&order=enviado_em.desc&limit=1",
                                  None, self._headers())
        except RuntimeError:
            self.controle_envio = False  # tabela envios não existe (migração 002 pendente)
            return None
        if not linhas:
            return None
        return datetime.fromisoformat(linhas[0]["enviado_em"].replace("Z", "+00:00"))

    def registrar_envio(self, ids: list[str], assunto: str, destinatarios: list[str]) -> None:
        """Marca as notícias como enviadas e registra o e-mail."""
        if not self.controle_envio:
            return
        agora = datetime.now(timezone.utc).isoformat()
        headers = self._headers({"Prefer": "return=minimal"})
        for i in range(0, len(ids), LOTE):
            lote = ids[i : i + LOTE]
            self._enviar("PATCH", f"/noticias?id=in.({','.join(lote)})", {"enviada_em": agora}, headers)
        self._enviar("POST", "/envios", {"enviado_em": agora, "assunto": assunto,
                                         "noticias": len(ids), "destinatarios": destinatarios}, headers)

    # ------------------------------------------------------------ escrita
    def _upsert(self, tabela: str, conflito: str, linhas: list[dict]) -> None:
        headers = self._headers({"Prefer": "resolution=merge-duplicates,return=minimal"})
        for i in range(0, len(linhas), LOTE):
            self._enviar("POST", f"/{tabela}?on_conflict={conflito}", linhas[i : i + LOTE], headers)

    def salvar(self, resultados: list[ResultadoTema]) -> int:
        noticias: dict[str, dict] = {}
        temas: list[dict] = []
        agora = datetime.now(timezone.utc).isoformat()
        for res in resultados:
            for m in res.noticias:
                nid = id_match(m)
                noticias.setdefault(nid, linha_noticia(nid, m, agora))
                temas.append({
                    "noticia_id": nid,
                    "tema": res.tema.id,
                    "filtros": m.filtros,
                    "palavras": m.palavras,
                })
        if noticias:
            self._upsert("noticias", "id", list(noticias.values()))
            self._upsert("noticias_temas", "noticia_id,tema", temas)
        return len(noticias)


def mapa_links(linhas: list[dict]) -> dict[str, str]:
    """Link (normalizado) -> id de cada notícia gravada, incluindo os links de
    "também em". Serve para reconhecer a mesma história quando outro jornal
    passa a ser o principal."""
    mapa: dict[str, str] = {}
    for linha in linhas:
        for link in [linha.get("link")] + [o.get("link") for o in linha.get("outros_links") or []]:
            if link:
                mapa.setdefault(chave_link(link), linha["id"])
    return mapa


def id_versao(link: str, titulo: str) -> str:
    """Id de uma nova versão da matéria: mesmo link, título diferente
    (alguns jornais atualizam a mesma página com conteúdo novo)."""
    return hashlib.sha1(f"{chave_link(link)}|{chave_titulo(titulo)}".encode()).hexdigest()


def versoes_gravadas(linhas: list[dict]) -> dict[str, list[dict]]:
    """Link (normalizado) -> versões gravadas: [{id, titulo, coletado}], a mais
    antiga primeiro. Só o link principal: é a data exata em que aquela
    matéria apareceu."""
    versoes: dict[str, list[dict]] = {}
    for linha in linhas:
        if not linha.get("link") or not linha.get("coletado_em"):
            continue
        try:
            quando = datetime.fromisoformat(linha["coletado_em"].replace("Z", "+00:00"))
        except ValueError:
            continue
        versoes.setdefault(chave_link(linha["link"]), []).append(
            {"id": linha["id"], "titulo": linha.get("titulo") or "", "coletado": quando})
    for lista in versoes.values():
        lista.sort(key=lambda v: v["coletado"])
    return versoes


def id_match(m: Match) -> str:
    """Id da notícia no banco: o já gravado para a história, ou o do link."""
    return m.id_banco or m.noticia.id_versao or id_noticia(m.noticia.link)


def alinhar_com_banco(resultados: list[ResultadoTema], mapa: dict[str, str]) -> int:
    """Se a história já está gravada (por qualquer um dos links do grupo),
    usa o mesmo registro em vez de criar outro. Devolve quantas mudaram."""
    n = 0
    for res in resultados:
        for m in res.noticias:
            if m.noticia.id_versao or chave_link(m.noticia.link) in mapa:
                continue
            eid = next((x.id_versao or mapa.get(chave_link(x.link)) for x in m.relacionadas
                        if x.id_versao or chave_link(x.link) in mapa), None)
            if not eid:
                continue
            n += 1
            mesma = next((x for x in m.relacionadas if (x.id_versao or id_noticia(x.link)) == eid), None)
            if mesma:  # o principal gravado antes está no grupo: volta a ser o principal
                m.relacionadas.remove(mesma)
                m.relacionadas.insert(0, m.noticia)
                m.noticia = mesma
            else:
                m.id_banco = eid
    return n


def linha_noticia(nid: str, m: Match, agora: str) -> dict:
    """Todas as linhas têm as mesmas colunas (exigência do upsert em lote).
    `coletado_em` fica de fora para manter a data da primeira coleta."""
    n = m.noticia
    fontes = list(dict.fromkeys(n.fontes + [f for r in m.relacionadas for f in r.fontes]))
    return {
        "id": nid,
        "titulo": n.titulo,
        "link": n.link,
        "fonte": n.fonte,
        "fontes": fontes,
        "outros_links": [{"fonte": r.fonte, "link": r.link, "titulo": r.titulo} for r in m.relacionadas],
        "categorias": n.categorias,
        "paises": m.paises,
        "publicado_em": n.publicado_em.isoformat() if n.publicado_em else None,
        "trecho_rss": n.resumo or None,
        "texto": n.texto or None,
        "resumo_ia": m.resumo_ia or None,
        "imagem": n.imagem,
        "atualizado_em": agora,
    }


def reaproveitar(resultados: list[ResultadoTema], existentes: dict[str, dict], resumos: bool = True) -> int:
    """Copia resumo/texto já gravados para não baixar nem resumir de novo."""
    n = 0
    for res in resultados:
        for m in res.noticias:
            linha = existentes.get(id_match(m))
            if not linha:
                continue
            if resumos and linha.get("resumo_ia") and not m.resumo_ia:
                m.resumo_ia = linha["resumo_ia"]
                n += 1
            if linha.get("texto") and not m.noticia.texto:
                m.noticia.texto = linha["texto"]
    return n
