"""Etapa 1: busca de notícias nos feeds RSS.

Lê os feeds listados em feeds.yaml, limpa o texto, descarta notícias antigas
e remove duplicadas (a mesma notícia publicada em mais de um feed).
"""

from __future__ import annotations

import calendar
import html
import re
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import feedparser
import yaml

from .texto import compilar, normalizar, primeira

USER_AGENT = "Mozilla/5.0 (compatible; n8news/1.0; +https://github.com/hvnasser/n8news)"
TAMANHO_RESUMO = 400


@dataclass
class Feed:
    fonte: str
    categoria: str
    url: str
    ativo: bool = True


@dataclass
class Config:
    feeds: list[Feed]
    janela_horas: float = 24
    max_por_feed: int = 30
    timeout_segundos: float = 15
    ignorar_links_com: list[str] = field(default_factory=list)
    google_news_fontes_permitidas: list[str] = field(default_factory=list)


@dataclass
class Noticia:
    titulo: str
    link: str
    resumo: str
    fonte: str
    categoria: str
    publicado_em: datetime | None
    fontes: list[str] = field(default_factory=list)
    categorias: list[str] = field(default_factory=list)
    imagem: str | None = None
    texto: str = ""  # texto extraído da página (etapa de artigo)
    autor: str = ""  # autor indicado no RSS ou na página
    data_pagina: str = ""  # data de publicação declarada pela página (AAAA-MM-DD), se houver

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["publicado_em"] = self.publicado_em.isoformat() if self.publicado_em else None
        return d


@dataclass
class StatusFeed:
    fonte: str
    categoria: str
    url: str
    status: str  # "ok", "vazio" ou "erro"
    noticias: int = 0
    erro: str = ""
    descartadas: int = 0  # Google News: de jornais fora da lista permitida


@dataclass
class Resultado:
    noticias: list[Noticia]
    status: list[StatusFeed]


def carregar_config(caminho: str | Path = "feeds.yaml") -> Config:
    dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    opcoes = dados.get("config") or {}
    feeds = [Feed(**f) for f in dados.get("feeds") or []]
    return Config(feeds=[f for f in feeds if f.ativo], **opcoes)


# ---------------------------------------------------------------- leitura

def baixar(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ler_feed(feed: Feed, timeout: float, baixar_fn=baixar):
    """Retorna (entradas, erro). Nunca levanta exceção: um feed quebrado não
    pode derrubar a busca inteira."""
    try:
        conteudo = baixar_fn(feed.url, timeout)
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"
    parsed = feedparser.parse(conteudo)
    if not parsed.entries and (parsed.bozo or not parsed.version):
        motivo = parsed.get("bozo_exception") or "o endereço não retornou um RSS/Atom"
        return [], f"feed inválido: {motivo}"
    return parsed.entries, ""


# ---------------------------------------------------------------- limpeza

def limpar_texto(texto: str | None) -> str:
    if not texto:
        return ""
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", html.unescape(sem_tags)).strip()


def cortar(texto: str, maximo: int = TAMANHO_RESUMO) -> str:
    if len(texto) <= maximo:
        return texto
    corte = texto[:maximo]
    espaco = corte.rfind(" ")
    if espaco > maximo * 0.6:
        corte = corte[:espaco]
    return corte.rstrip(" ,.;:") + "…"


_PARAMS_RASTREIO = re.compile(r"^(utm_|fbclid|gclid|cmpid|origin|ref)", re.I)


def chave_link(link: str) -> str:
    """Normaliza o link para detectar a mesma notícia em feeds diferentes."""
    partes = urlsplit(link.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(partes.query) if not _PARAMS_RASTREIO.match(k)])
    host = partes.netloc.lower().removeprefix("www.")
    caminho = partes.path.rstrip("/")
    return f"{host}{caminho}?{query}".rstrip("?").lower()


def chave_titulo(titulo: str) -> str:
    sem_acento = unicodedata.normalize("NFD", titulo.lower())
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", sem_acento).strip()


def data_da_entrada(entrada) -> datetime | None:
    for campo in ("published_parsed", "updated_parsed"):
        valor = entrada.get(campo)
        if valor:
            return datetime.fromtimestamp(calendar.timegm(valor), tz=timezone.utc)
    return None


def imagem_da_entrada(entrada) -> str | None:
    for midia in entrada.get("media_content") or []:
        if midia.get("url"):
            return midia["url"]
    for midia in entrada.get("media_thumbnail") or []:
        if midia.get("url"):
            return midia["url"]
    for anexo in entrada.get("enclosures") or []:
        if str(anexo.get("type", "")).startswith("image") and anexo.get("href"):
            return anexo["href"]
    return None


def limpar_google_news(entrada, titulo: str, resumo: str) -> tuple[str, str, str]:
    """Feeds do Google News trazem o título como "Manchete - Jornal" e um
    resumo que só repete a manchete. Remove o sufixo, descarta esse resumo e
    devolve o nome do jornal de origem."""
    origem = limpar_texto((entrada.get("source") or {}).get("title"))
    if origem and titulo.endswith(f" - {origem}"):
        titulo = titulo[: -len(origem) - 3].strip()
    elif " - " in titulo:
        titulo = titulo.rsplit(" - ", 1)[0].strip()
    if chave_titulo(resumo).startswith(chave_titulo(titulo)):
        resumo = ""
    return titulo, resumo, origem


# ---------------------------------------------------------------- busca

def buscar_noticias(config: Config, agora: datetime | None = None, baixar_fn=baixar) -> Resultado:
    agora = agora or datetime.now(timezone.utc)
    permitidas = compilar(config.google_news_fontes_permitidas)
    limite_segundos = config.janela_horas * 3600

    with ThreadPoolExecutor(max_workers=8) as pool:
        lidos = list(pool.map(lambda f: ler_feed(f, config.timeout_segundos, baixar_fn), config.feeds))

    noticias: list[Noticia] = []
    por_link: dict[str, Noticia] = {}
    por_titulo: dict[str, Noticia] = {}
    status: list[StatusFeed] = []

    for feed, (entradas, erro) in zip(config.feeds, lidos):
        st = StatusFeed(feed.fonte, feed.categoria, feed.url, "erro" if erro else "vazio", erro=erro)
        status.append(st)

        for entrada in entradas[: config.max_por_feed]:
            titulo = limpar_texto(entrada.get("title"))
            link = (entrada.get("link") or "").strip()
            if not titulo or not link:
                continue
            if any(trecho in link for trecho in config.ignorar_links_com):
                continue  # ex: conteúdo patrocinado

            data = data_da_entrada(entrada)
            if data and (agora - data).total_seconds() > limite_segundos:
                continue  # notícia antiga (sem data: mantém)

            st.noticias += 1

            resumo = cortar(limpar_texto(entrada.get("summary") or entrada.get("description")))
            fonte = feed.fonte
            if "news.google.com" in link:
                titulo, resumo, origem = limpar_google_news(entrada, titulo, resumo)
                if permitidas and origem and not primeira(permitidas, normalizar(origem)):
                    st.descartadas += 1
                    st.noticias -= 1
                    continue  # jornal fora da lista de fontes confiáveis
                if feed.fonte == "Google News" and origem:
                    fonte = origem  # busca genérica: mostra o jornal de verdade
            k_link, k_titulo = chave_link(link), chave_titulo(titulo)
            existente = por_link.get(k_link) or por_titulo.get(k_titulo)
            if existente:
                # Mesma notícia em outro feed: só registra a fonte/categoria extra
                if feed.categoria not in existente.categorias:
                    existente.categorias.append(feed.categoria)
                if fonte not in existente.fontes:
                    existente.fontes.append(fonte)
                if not existente.resumo:
                    existente.resumo = resumo
                continue

            n = Noticia(
                titulo=titulo,
                link=link,
                resumo=resumo,
                fonte=fonte,
                categoria=feed.categoria,
                publicado_em=data,
                fontes=[fonte],
                categorias=[feed.categoria],
                imagem=imagem_da_entrada(entrada),
                autor=limpar_texto(entrada.get("author")),
            )
            por_link[k_link] = n
            por_titulo[k_titulo] = n
            noticias.append(n)

    for st in status:
        if st.noticias and not st.erro:
            st.status = "ok"

    # Mais recentes primeiro; sem data vão para o fim
    vazio = datetime.min.replace(tzinfo=timezone.utc)
    noticias.sort(key=lambda n: n.publicado_em or vazio, reverse=True)
    ordem = {"erro": 0, "vazio": 1, "ok": 2}
    status.sort(key=lambda s: ordem[s.status])
    return Resultado(noticias, status)
