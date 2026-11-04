"""Etapa 3: IA sobre as notícias de cada tema, em duas tarefas.

1. relevancia: confirma se a notícia é mesmo do tema (a palavra-chave pode ter
   aparecido por acaso). Tarefa simples: dá para usar um modelo barato.
   Também marca artigos de opinião e quem assina.
2. resumo: 1-2 frases em português para cada notícia que ficou. Notícia sem
   texto (só título) não vai para a IA: o resumo é o próprio título.
   Opinião ganha o prefixo "Opinião — <autor>:".

Cada tarefa tem seu modelo (temas.yaml → ia.tarefas). Um tema pode trocar o
modelo de uma tarefa só para ele (temas.yaml → tema.ia). Notícias que já
têm resumo gravado no banco não passam pela IA de novo.

Sem chave, ou se a chamada falhar, tudo segue com o trecho original do RSS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ia import ErroCredencial, ErroIA, criar_cliente
from .temas import ResultadoTema

CONTEXTO = """Você ajuda a montar um painel diário de notícias lido por um \
profissional do mercado financeiro (Brasil e principais economias). Você recebe \
um tema (nome e descrição do que entra nele) e uma lista de notícias (id, \
título, fontes e, quando existe, um trecho do texto)."""

SISTEMA_RELEVANCIA = CONTEXTO + """

Para cada notícia, responda:
- relevante: true se trata do tema, conforme a descrição do tema; false se a \
palavra-chave apareceu por acaso (sentido figurado, finanças pessoais, \
recomendação de investimento, assunto sem relação ou o que a descrição \
disser que fica fora).
- opiniao: true se é artigo de opinião, coluna, editorial ou análise assinada \
(não reportagem). Pistas: "opinião", "coluna", "editorial", "Opinion |", \
"/opiniao/" ou "/colunas/" no link, texto em primeira pessoa com argumentação.
- autor: nome de quem assina a opinião (pessoa, ou "Editorial do <jornal>" para \
editoriais), usando o campo autor ou o texto. Deixe vazio se não for opinião \
ou se não houver como saber. Não invente nomes.

Devolva um item para cada notícia recebida, com o mesmo id."""

SISTEMA_RESUMO = CONTEXTO + """

Para cada notícia, escreva o campo resumo: 1 a 2 frases em português do Brasil, \
no máximo 280 caracteres. Diga o fato principal com os detalhes relevantes para \
o tema que estiverem no texto (quem, valores, decisões, números, prazos, país). Use só a informação recebida: não invente números, datas nem \
desdobramentos. Se só houver o título, escreva uma frase clara a partir dele, sem \
acrescentar fatos. Notícias em inglês também são resumidas em português. Não \
comece com "A notícia" ou "O artigo".

Devolva um item para cada notícia recebida, com o mesmo id."""

SISTEMA_JUNTO = SISTEMA_RELEVANCIA.replace(
    "Devolva um item para cada notícia recebida, com o mesmo id.", "") + """\
- resumo: se relevante for true e houver trecho, 1 a 2 frases em português do \
Brasil, no máximo 280 caracteres, com o fato principal e os detalhes relevantes \
para o tema que estiverem no texto (quem, valores, decisões, números, prazos, \
país). Use só a informação recebida: não invente números, datas nem \
desdobramentos. Notícias em inglês também são resumidas em português. Não \
comece com "A notícia" ou "O artigo". Se relevante for false ou não houver \
trecho, deixe vazio.

Devolva um item para cada notícia recebida, com o mesmo id."""


def _schema(campo: str, tipo: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "itens": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, campo: {"type": tipo}},
                    "required": ["id", campo],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["itens"],
        "additionalProperties": False,
    }


SCHEMA_RELEVANCIA = {
    "type": "object",
    "properties": {
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevante": {"type": "boolean"},
                    "opiniao": {"type": "boolean"},
                    "autor": {"type": "string"},
                },
                "required": ["id", "relevante", "opiniao", "autor"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["itens"],
    "additionalProperties": False,
}
SCHEMA_RESUMO = _schema("resumo", "string")
SCHEMA_JUNTO = {
    "type": "object",
    "properties": {
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevante": {"type": "boolean"},
                    "opiniao": {"type": "boolean"},
                    "autor": {"type": "string"},
                    "resumo": {"type": "string"},
                },
                "required": ["id", "relevante", "opiniao", "autor", "resumo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["itens"],
    "additionalProperties": False,
}
SCHEMA_SECUNDARIAS = _schema("veredito", "string")

SISTEMA_SECUNDARIAS = CONTEXTO + """

Numa seção do tema, há notícias de fontes PRINCIPAIS (jornais preferidos) e \
CANDIDATAS de fontes secundárias. O leitor só quer uma candidata se ela trouxer \
um assunto bem distinto do que as principais já cobrem. Para cada candidata, \
responda veredito:
- "repete" se o fato ou assunto central já aparece em alguma principal, mesmo \
com outras palavras, outro ângulo ou detalhes extras (ex: mais uma prévia da \
mesma reunião do Copom, mais uma repercussão da mesma declaração), ou se outra \
candidata trata do mesmo assunto e parece mais completa (entre candidatas \
parecidas, só a melhor fica como "distinta");
- "distinta" só se for um fato ou assunto que nenhuma principal cobre."""

SCHEMA_AGRUPAR = _schema("grupo", "integer")

SISTEMA_AGRUPAR = CONTEXTO + """

Você recebe os títulos das notícias de uma seção do tema. Junte no mesmo grupo \
as que trazem o MESMO conteúdo: o mesmo fato, anúncio ou decisão. Todas as \
prévias de uma mesma decisão (o que o mercado espera, se está dividido, por que \
o banco central pode cortar ou manter, projeções de bancos) são o mesmo \
conteúdo e vão no mesmo grupo, mesmo com ângulos diferentes; o mesmo vale para \
as repercussões de uma mesma decisão ou declaração. Os resumos do dia do \
mercado (bolsa, dólar e juros "à espera de" um evento) formam um grupo próprio, \
separado das prévias. Ficam sozinhos: notícias de outro fato do mesmo assunto, \
agendas do dia e colunas/artigos de opinião (cada um no seu grupo).
Responda grupo (número inteiro) para cada notícia: mesmo número = mesmo \
conteúdo. Devolva um item para cada notícia, com o mesmo id."""

PRIMEIRA_ETAPA = """
Você só tem os títulos. Decida pelo título sempre que der. Use "duvida" apenas \
quando o título não permitir saber se o assunto é o mesmo (aí o texto da \
matéria será lido numa segunda etapa). Devolva um item para cada candidata, com \
o mesmo id."""

SEGUNDA_ETAPA = """
Agora você tem também um trecho do texto de cada candidata (e das principais, \
quando houver). Responda só "distinta" ou "repete"; se ainda houver dúvida, \
"repete". Devolva um item para cada candidata, com o mesmo id."""

TRECHO_MAX = 3000  # caracteres do texto da matéria enviados por notícia


@dataclass
class Tarefa:
    modelo: str
    ativo: bool = True
    esforco: str | None = None  # low | medium | high (modelos com raciocínio)


TAREFAS_PADRAO = {
    "relevancia": Tarefa("anthropic/claude-haiku-5.5", esforco="low"),
    "secundarias": Tarefa("anthropic/claude-haiku-5.5", esforco="low"),
    "agrupar": Tarefa("anthropic/claude-haiku-5.5", esforco="low"),
    "resumo": Tarefa("anthropic/claude-opus-5.5", esforco="low"),
}


@dataclass
class ConfigIA:
    provedor: str = "openrouter"
    tarefas: dict[str, Tarefa] = field(default_factory=lambda: dict(TAREFAS_PADRAO))
    remover_irrelevantes: bool = True
    max_por_chamada: int = 40

    def tarefa(self, nome: str, tema=None) -> Tarefa:
        """Configuração da tarefa, com a troca de modelo do tema, se houver."""
        base = self.tarefas.get(nome) or TAREFAS_PADRAO[nome]
        troca = ((getattr(tema, "ia", None) or {}).get(nome)) or {}
        return Tarefa(**{**base.__dict__, **troca})


def carregar_config_ia(caminho: str | Path = "temas.yaml") -> ConfigIA:
    dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8")) or {}
    bruto = dict(dados.get("ia") or {})
    tarefas = dict(TAREFAS_PADRAO)
    for nome, valores in (bruto.pop("tarefas", None) or {}).items():
        tarefas[nome] = Tarefa(**{**TAREFAS_PADRAO.get(nome, Tarefa("")).__dict__, **(valores or {})})
    return ConfigIA(tarefas=tarefas, **bruto)


def _trecho(m) -> str:
    """Texto disponível da notícia: matéria, trecho do RSS ou de outro jornal."""
    n = m.noticia
    return n.texto or n.resumo or next((r.resumo for r in m.relacionadas if r.resumo), "")


def _com_opiniao(resumo: str, autor: str) -> str:
    rotulo = f"Opinião — {autor}" if autor else "Opinião (autor não identificado)"
    return f"{rotulo}: {resumo}"


def _entrada(tema, lote) -> str:
    noticias = []
    for i, m in enumerate(lote):
        n = m.noticia
        fontes = list(dict.fromkeys(n.fontes + [f for r in m.relacionadas for f in r.fontes]))
        item = {"id": i, "titulo": n.titulo, "fontes": fontes, "trecho": _trecho(m)[:TRECHO_MAX]}
        if n.autor:
            item["autor"] = n.autor
        if "news.google.com" not in n.link:
            item["link"] = n.link
        noticias.append(item)
    cab = {"tema": tema.nome, "descricao": tema.descricao} if hasattr(tema, "nome") else {"tema": tema}
    return json.dumps({**cab, "noticias": noticias}, ensure_ascii=False)


def _rodar(cliente, tarefa: Tarefa, sistema: str, schema: dict, campo: str, tema,
           noticias: list, tamanho: int, avisos: list[str], nome_tarefa: str):
    """Chama o modelo em lotes e devolve {match: valor}."""
    valores = {}
    for inicio in range(0, len(noticias), tamanho):
        lote = noticias[inicio : inicio + tamanho]
        try:
            dados = cliente.json(modelo=tarefa.modelo, sistema=sistema, usuario=_entrada(tema, lote),
                                 schema=schema, esforco=tarefa.esforco)
            itens = dados["itens"]
        except ErroCredencial:
            raise
        except (ErroIA, KeyError, TypeError) as e:
            avisos.append(f"{getattr(tema, 'nome', tema)} / {nome_tarefa} ({tarefa.modelo}): {e}")
            continue
        for item in itens:
            i = item.get("id") if isinstance(item, dict) else None
            if isinstance(i, int) and 0 <= i < len(lote) and campo in item:
                valores[id(lote[i])] = item
    return valores


def _secundarias_lote(cliente, tarefa: Tarefa, res: ResultadoTema, secao: str, principais: list,
                      lote: list, com_texto: bool) -> dict[int, str]:
    """Uma chamada: {índice da candidata: "distinta" | "repete" | "duvida"}."""
    def item(m, i=None) -> dict:
        n = m.noticia
        d = {"titulo": n.titulo, "fontes": n.fontes}
        if com_texto:
            d["trecho"] = _trecho(m)[:1500]
        return {"id": i, **d} if i is not None else d

    entrada = json.dumps({"tema": res.tema.nome, "descricao": res.tema.descricao, "secao": secao,
                          "principais": [item(m) for m in principais[:60]],
                          "candidatas": [item(m, i) for i, m in enumerate(lote)]}, ensure_ascii=False)
    sistema = SISTEMA_SECUNDARIAS + (SEGUNDA_ETAPA if com_texto else PRIMEIRA_ETAPA)
    dados = cliente.json(modelo=tarefa.modelo, sistema=sistema, usuario=entrada,
                         schema=SCHEMA_SECUNDARIAS, esforco=tarefa.esforco)
    vereditos = {}
    for it in dados["itens"]:
        i = it.get("id") if isinstance(it, dict) else None
        v = str(it.get("veredito", "")).lower() if isinstance(it, dict) else ""
        if isinstance(i, int) and 0 <= i < len(lote) and v in ("distinta", "repete", "duvida"):
            vereditos[i] = v
    return vereditos


def filtrar_secundarias(resultados: list[ResultadoTema], cfg: ConfigIA, prioridade, cliente=None,
                        baixar_textos=None, memoria=None) -> list[str]:
    """Por seção de cada tema: notícias de fontes secundárias (fora da
    prioridade dos jornais) só ficam se trouxerem assunto bem distinto das
    fontes principais (ou, sem principal, se não repetirem outra secundária).
    1ª etapa só pelo título; na dúvida, baixa o texto (`baixar_textos`) e a IA
    decide de novo. Continuou na dúvida: sai.
    Com `memoria`, cada matéria é avaliada uma vez só: nas buscas seguintes
    vale o veredito guardado, e a IA só é chamada para matérias novas.
    Devolve avisos."""
    def principal(m) -> bool:
        return any(prioridade.principal(n) for n in [m.noticia] + m.relacionadas)

    def tirar(res, fora: set[int]) -> None:
        if fora:
            res.excluidas += [(m.noticia, "IA: repete as fontes principais") for m in res.noticias if id(m) in fora]
            res.noticias = [m for m in res.noticias if id(m) not in fora]

    trabalhos = []
    for res in resultados:
        if not cfg.tarefa("secundarias", res.tema).ativo:
            continue
        secoes: dict[str, list] = {}
        for m in res.noticias:
            secoes.setdefault(m.filtros[0] if m.filtros else "", []).append(m)
        ja_repetidas: set[int] = set()
        for secao, lista in secoes.items():
            principais = [m for m in lista if principal(m)]
            novas, aceitas = [], []
            for m in lista:
                if principal(m) or m.garantida:
                    continue
                v = memoria.veredito(res.tema.id, m.noticia.link) if memoria else None
                if v == "repete":
                    ja_repetidas.add(id(m))
                elif v == "distinta":
                    aceitas.append(m)
                else:
                    novas.append(m)
            # As já aceitas entram como referência (já estão no painel)
            referencia = principais + aceitas
            if novas and (referencia or len(novas) >= 2):
                trabalhos.append((res, secao, referencia, novas))
            elif novas and memoria:  # sozinha na seção: fica
                for m in novas:
                    memoria.guardar_veredito(res.tema.id, m.noticia.link, "distinta")
        tirar(res, ja_repetidas)
    if not trabalhos:
        return []
    if cliente is None:
        try:
            cliente = criar_cliente(cfg.provedor)
        except (ErroCredencial, ErroIA) as e:
            return [f"Filtro de fontes secundárias não rodou: {e}"]

    avisos: list[str] = []
    try:
        for res, secao, principais, candidatas in trabalhos:
            tarefa = cfg.tarefa("secundarias", res.tema)
            fora: set[int] = set()
            for inicio in range(0, len(candidatas), cfg.max_por_chamada):
                lote = candidatas[inicio : inicio + cfg.max_por_chamada]
                try:
                    v1 = _secundarias_lote(cliente, tarefa, res, secao, principais, lote, com_texto=False)
                    vereditos = {i: v for i, v in v1.items() if v != "duvida"}
                    duvida = [(i, m) for i, m in enumerate(lote) if v1.get(i) == "duvida"]
                    if duvida:
                        if baixar_textos:
                            baixar_textos([m.noticia for _, m in duvida])
                        v2 = _secundarias_lote(cliente, tarefa, res, secao, principais,
                                               [m for _, m in duvida], com_texto=True)
                        for k, (i, _) in enumerate(duvida):
                            vereditos[i] = "distinta" if v2.get(k) == "distinta" else "repete"
                except ErroCredencial:
                    raise
                except (ErroIA, KeyError, TypeError) as e:
                    avisos.append(f"{res.tema.nome} / fontes secundárias ({tarefa.modelo}): {e}")
                    continue
                for i, v in vereditos.items():
                    if v == "repete":
                        fora.add(id(lote[i]))
                    if memoria:
                        memoria.guardar_veredito(res.tema.id, lote[i].noticia.link, v)
            tirar(res, fora)
    except ErroCredencial as e:
        avisos.append(f"Filtro de fontes secundárias não rodou: {e}")
    return avisos


def _juntar_na_lista(res: ResultadoTema, membros: list, prioridade):
    """Troca os membros pelo cartão juntado, na posição da primeira matéria."""
    from .temas import juntar_grupo
    ids = {id(m) for m in membros}
    principal = juntar_grupo(membros, prioridade)
    novas = []
    for m in res.noticias:
        if id(m) not in ids:
            novas.append(m)
        elif principal not in novas:
            novas.append(principal)
    res.noticias = novas
    return principal


def _links(m) -> list[str]:
    return [m.noticia.link] + [r.link for r in m.relacionadas]


def juntar_mesmo_conteudo(resultados: list[ResultadoTema], cfg: ConfigIA, prioridade, cliente=None,
                          memoria=None) -> list[str]:
    """Por seção de cada tema, a IA (só pelos títulos) junta num cartão as
    notícias com o mesmo conteúdo, mesmo de fontes principais. O link principal
    segue a prioridade dos jornais; as outras vão para "também em".
    Colunistas confirmados não são juntados.
    Com `memoria`: os cartões já feitos antes são refeitos sem IA, e a IA só é
    chamada numa seção que tenha matéria nova. Devolve avisos."""
    trabalhos = []
    for res in resultados:
        if not cfg.tarefa("agrupar", res.tema).ativo:
            continue
        if memoria:  # refaz os cartões de buscas anteriores, sem IA
            por_cartao: dict[str, list] = {}
            for m in res.noticias:
                c = next((c for l in _links(m) if (c := memoria.cartao(res.tema.id, l))), None)
                if c and not m.garantida:
                    por_cartao.setdefault(c, []).append(m)
            for membros in por_cartao.values():
                if len(membros) > 1:
                    _juntar_na_lista(res, membros, prioridade)
        secoes: dict[str, list] = {}
        for m in res.noticias:
            if not m.garantida:
                secoes.setdefault(m.filtros[0] if m.filtros else "", []).append(m)
        for secao, lista in secoes.items():
            if len(lista) < 2:
                continue
            if memoria and all(memoria.vista(res.tema.id, m.noticia.link) for m in lista):
                continue  # nada novo na seção desde a última busca
            trabalhos.append((res, secao, lista))
    if not trabalhos:
        return []
    if cliente is None:
        try:
            cliente = criar_cliente(cfg.provedor)
        except (ErroCredencial, ErroIA) as e:
            return [f"Junção por conteúdo não rodou: {e}"]

    avisos: list[str] = []
    for res, secao, lista in trabalhos:
        tarefa = cfg.tarefa("agrupar", res.tema)
        lista = lista[:80]
        entrada = json.dumps({"tema": res.tema.nome, "descricao": res.tema.descricao, "secao": secao,
                              "noticias": [{"id": i, "titulo": m.noticia.titulo, "fontes": m.noticia.fontes}
                                           for i, m in enumerate(lista)]}, ensure_ascii=False)
        try:
            dados = cliente.json(modelo=tarefa.modelo, sistema=SISTEMA_AGRUPAR, usuario=entrada,
                                 schema=SCHEMA_AGRUPAR, esforco=tarefa.esforco)
            grupos: dict[int, list] = {}
            for it in dados["itens"]:
                i, g = (it.get("id"), it.get("grupo")) if isinstance(it, dict) else (None, None)
                if isinstance(i, int) and 0 <= i < len(lista) and isinstance(g, int):
                    grupos.setdefault(g, []).append(lista[i])
        except ErroCredencial as e:
            return avisos + [f"Junção por conteúdo não rodou: {e}"]
        except (ErroIA, KeyError, TypeError) as e:
            avisos.append(f"{res.tema.nome} / juntar mesmo conteúdo ({tarefa.modelo}): {e}")
            continue
        for membros in grupos.values():
            cartao = _juntar_na_lista(res, membros, prioridade) if len(membros) > 1 else membros[0]
            if memoria:
                if len(membros) > 1:
                    memoria.guardar_cartao(res.tema.id, _links(cartao), _links(cartao)[0])
                memoria.marcar_vistas(res.tema.id, _links(cartao))
    return avisos


def resumir_temas(resultados: list[ResultadoTema], cfg: ConfigIA, cliente=None) -> list[str]:
    """Aplica relevância e resumo. Devolve avisos (vazio = tudo ok)."""
    pendentes_total = sum(1 for r in resultados for m in r.noticias if not m.resumo_ia or m.so_busca)
    if not pendentes_total:
        return []
    if not any(cfg.tarefa(n).ativo for n in ("relevancia", "resumo")):
        return ["IA desligada (temas.yaml → ia.tarefas)"]

    if cliente is None:
        try:
            cliente = criar_cliente(cfg.provedor)
        except (ErroCredencial, ErroIA) as e:
            return [f"IA não rodou: {e}"]

    avisos: list[str] = []
    try:
        for res in resultados:
            nome = res.tema.nome
            pendentes = [m for m in res.noticias if not m.resumo_ia]  # já resumidas: reaproveita
            # Entrou só pela busca pelo nome (sem palavra-chave nem autor confirmado):
            # a IA sempre confere, mesmo se o resumo já estava gravado
            conferir = pendentes + [m for m in res.noticias if m.resumo_ia and m.so_busca]

            relev = cfg.tarefa("relevancia", res.tema)
            resumo = cfg.tarefa("resumo", res.tema)
            opiniao: dict[int, str] = {}  # id(match) -> autor
            junto = relev.ativo and resumo.ativo  # uma chamada só: cada notícia vai uma vez
            if relev.ativo and conferir:
                if junto:
                    vals = _rodar(cliente, resumo, SISTEMA_JUNTO, SCHEMA_JUNTO, "relevante",
                                  res.tema, conferir, cfg.max_por_chamada, avisos, "relevância e resumo")
                else:
                    vals = _rodar(cliente, relev, SISTEMA_RELEVANCIA, SCHEMA_RELEVANCIA, "relevante",
                                  res.tema, conferir, cfg.max_por_chamada, avisos, "relevância")
                pend_ids = {id(m) for m in pendentes}
                for m in conferir:
                    item = vals.get(id(m))
                    if item:
                        # Colunista/busca dedicada: entra sempre, mesmo se a IA discordar
                        m.relevante = bool(item["relevante"]) or m.garantida
                        if item.get("opiniao"):
                            opiniao[id(m)] = str(item.get("autor") or m.noticia.autor or "").strip()
                        texto_resumo = str(item.get("resumo") or "").strip()
                        if junto and texto_resumo and id(m) in pend_ids and _trecho(m).strip():
                            m.resumo_ia = texto_resumo
                if cfg.remover_irrelevantes:
                    fora = [m for m in res.noticias if not m.relevante]
                    res.noticias = [m for m in res.noticias if m.relevante]
                    res.excluidas += [(m.noticia, "IA: fora do tema") for m in fora]
                    pendentes = [m for m in pendentes if m.relevante]


            # Só título (sem texto nem trecho): o resumo é o próprio título, sem IA
            so_titulo = [m for m in pendentes if not _trecho(m).strip()]
            for m in so_titulo:
                m.resumo_ia = m.noticia.titulo
            com_texto = [m for m in pendentes if m not in so_titulo and not m.resumo_ia]

            # Resumo à parte só se a relevância estiver desligada (com as duas ligadas,
            # o resumo já veio na chamada junta)
            if resumo.ativo and com_texto and not junto:
                vals = _rodar(cliente, resumo, SISTEMA_RESUMO, SCHEMA_RESUMO, "resumo",
                              res.tema, com_texto, cfg.max_por_chamada, avisos, "resumo")
                for m in com_texto:
                    if id(m) in vals:
                        m.resumo_ia = str(vals[id(m)]["resumo"]).strip()

            for m in pendentes:
                if id(m) in opiniao and m.resumo_ia:
                    m.resumo_ia = _com_opiniao(m.resumo_ia, opiniao[id(m)])
    except ErroCredencial as e:
        avisos.append(f"IA não rodou: {e}")
    return avisos
