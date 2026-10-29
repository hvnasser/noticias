"""Uso:
    python -m n8news                  # notícias separadas por tema
    python -m n8news --todas          # todas as notícias, sem filtro de tema
    python -m n8news --json saida.json
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .artigo import baixar_textos
from .banco import Supabase, alinhar_com_banco, id_match, mapa_links, primeiras_coletas, reaproveitar
from .busca import buscar_noticias, carregar_config
from .envio import enviar_gmail, montar_email, selecionar_novas
from .painel import gerar_painel
from .repetidas import remover_datas_antigas, remover_ja_vistas
from .paises import carregar_paises
from .resumo import carregar_config_ia, resumir_temas
from .temas import carregar_temas, classificar


JANELA_REPETIDAS = 7  # dias: procura no banco a mesma matéria/história gravada nesse período


def quando(n) -> str:
    return n.publicado_em.astimezone().strftime("%d/%m %H:%M") if n.publicado_em else "--"


def imprimir_noticia(n, extra: str = "", resumo: str = "") -> None:
    print(f"- [{quando(n)}] {n.titulo}  ({', '.join(n.fontes)}){extra}")
    print(f"  {n.link}")
    if resumo:
        print(f"  ✎ {resumo}")
    elif n.resumo:
        print(f"  {n.resumo[:200]}")


def imprimir_todas(noticias) -> None:
    por_categoria = defaultdict(list)
    for n in noticias:
        por_categoria[n.categoria].append(n)
    for categoria, lista in por_categoria.items():
        print(f"\n=== {categoria.upper()} ({len(lista)}) ===")
        for n in lista:
            imprimir_noticia(n)


def imprimir_temas(resultados) -> None:
    for res in resultados:
        print(f"\n{'=' * 70}\n{res.tema.nome.upper()} — {len(res.noticias)} notícias\n{'=' * 70}")
        # Agrupa pelo primeiro filtro que casou (na ordem do temas.yaml)
        grupos = defaultdict(list)
        for m in res.noticias:
            grupos[m.filtros[0]].append(m)
        for filtro in res.tema.filtros:
            lista = grupos.get(filtro.nome)
            if not lista:
                continue
            print(f"\n--- {filtro.nome} ({len(lista)}) ---")
            for m in lista:
                motivo = "; ".join(f"{f}: {p}" for f, p in zip(m.filtros, m.palavras))
                outras = sorted({f for r in m.relacionadas for f in r.fontes} - set(m.noticia.fontes))
                extra = f"\n  ↳ {motivo}"
                if m.paises:
                    extra += f"\n  ↳ países: {', '.join(m.paises)}"
                if outras:
                    extra += f"\n  ↳ também em: {', '.join(outras)}"
                imprimir_noticia(m.noticia, extra, m.resumo_ia)
        if res.excluidas:
            print(f"\n--- Excluídas pelo filtro 'excluir' ({len(res.excluidas)}) ---")
            for n, palavra in res.excluidas:
                print(f"- {n.titulo}  [{palavra}]")


def _mostrar_removidas(removidas, regra: str) -> None:
    if removidas:
        print(f"Repetidas ({regra}): {len(removidas)} matérias antigas removidas")
        for n, motivo in removidas:
            print(f"  - {n.titulo}  ({', '.join(n.fontes)}; {motivo})")


def processar_temas(por_tema, args, timeout, janela_horas=24):
    """Texto das matérias, resumo com IA e gravação no Supabase.
    Devolve (avisos, banco ou None, ids de notícias já enviadas por e-mail)."""
    avisos: list[str] = []
    ja_enviadas: set[str] = set()
    banco = None if args.sem_banco else Supabase.do_ambiente()
    if banco is None and not args.sem_banco:
        avisos.append("Supabase não configurado (SUPABASE_URL / SUPABASE_KEY): notícias não foram gravadas")

    if banco:
        try:
            recentes = banco.recentes(datetime.now(timezone.utc) - timedelta(days=JANELA_REPETIDAS))
            # Matéria que já apareceu há mais de 24h (ex: voltou nas recomendadas do site)
            _mostrar_removidas(remover_ja_vistas(por_tema, primeiras_coletas(recentes), horas=janela_horas),
                               "já vistas no banco")
            # Mesma história já gravada com outro link principal: usa o mesmo registro
            if (n := alinhar_com_banco(por_tema, mapa_links(recentes))):
                print(f"Banco: {n} notícias já gravadas com outro link (não serão duplicadas)")
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Supabase: não consegui checar repetidas ({e})")
        try:
            existentes = banco.existentes(list({id_match(m) for res in por_tema for m in res.noticias}))
            n = reaproveitar(por_tema, existentes, resumos=not args.refazer_resumos)
            ja_enviadas = {i for i, linha in existentes.items() if linha.get("enviada_em")}
            print(f"Banco: {len(existentes)} notícias já gravadas ({n} resumos reaproveitados)")
        except Exception as e:  # noqa: BLE001
            # Sem conseguir ler, não grava (evita apagar resumos antigos)
            avisos.append(f"Supabase: erro ao ler ({e}); nada será gravado")
            banco = None

    if not args.sem_texto:
        ok = baixar_textos([m.noticia for res in por_tema for m in res.noticias if not m.resumo_ia], timeout)
        print(f"Texto das matérias: {ok} baixados")
        # Data declarada pela própria página, antes da IA (não gasta resumo com notícia velha)
        _mostrar_removidas(remover_datas_antigas(por_tema, horas=janela_horas), "data da página")

    if not args.sem_resumo:
        avisos += resumir_temas(por_tema, carregar_config_ia(args.temas))

    if banco:
        try:
            print(f"Banco: {banco.salvar(por_tema)} notícias gravadas")
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Supabase: erro ao gravar ({e})")
            banco = None
    return avisos, banco, ja_enviadas


def processar_email(por_tema, args, banco, ja_enviadas) -> list[str]:
    """Monta (e envia) o e-mail só com notícias ainda não enviadas."""
    avisos: list[str] = []
    desde = banco.ultimo_envio() if banco else None
    controle = bool(banco and banco.controle_envio)
    novas = selecionar_novas(por_tema, ja_enviadas, id_match) if controle else por_tema
    total = sum(len(r.noticias) for r in novas)
    assunto, corpo_html, corpo_texto = montar_email(novas, desde=desde)
    if args.salvar_email:
        Path(args.salvar_email).write_text(corpo_html, encoding="utf-8")
        print(f"E-mail salvo em {args.salvar_email} ({total} notícias)")
    if not args.enviar_email:
        return avisos
    if not controle:
        avisos.append("controle de envio desligado (rode supabase/migracoes/002_envios.sql no Supabase): "
                      "o e-mail traz todas as notícias das últimas horas, mesmo as já enviadas")
    if total == 0:
        print("E-mail não enviado: nenhuma notícia nova desde o último envio")
        return avisos
    try:
        para = enviar_gmail(assunto, corpo_html, corpo_texto)
        print(f"E-mail enviado para {len(para)} destinatário(s): {assunto}")
    except Exception as e:  # noqa: BLE001
        return avisos + [f"E-mail não enviado: {e}"]
    if controle:
        try:
            ids = list({id_match(m) for r in novas for m in r.noticias})
            banco.registrar_envio(ids, assunto, para)
            print(f"Banco: {len(ids)} notícias marcadas como enviadas")
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Supabase: e-mail enviado, mas não consegui marcar as notícias ({e})")
    return avisos


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="n8news", description="Busca notícias e separa por tema.")
    p.add_argument("--config", default="feeds.yaml", help="arquivo de feeds (padrão: feeds.yaml)")
    p.add_argument("--temas", default="temas.yaml", help="arquivo de temas (padrão: temas.yaml)")
    p.add_argument("--paises", default="paises.yaml", help="arquivo de países (padrão: paises.yaml)")
    p.add_argument("--sem-resumo", action="store_true", help="não chama a IA para resumir")
    p.add_argument("--sem-texto", action="store_true", help="não baixa o texto das matérias")
    p.add_argument("--sem-banco", action="store_true", help="não lê nem grava no Supabase")
    p.add_argument("--painel", metavar="PASTA", help="gera o painel (index.html) nesta pasta")
    p.add_argument("--painel-dias", type=int, default=30, help="dias de histórico no painel (padrão: 30)")
    p.add_argument("--enviar-email", action="store_true", help="envia o e-mail do dia pelo Gmail")
    p.add_argument("--salvar-email", metavar="ARQUIVO", help="salva o HTML do e-mail (para conferir)")
    p.add_argument("--refazer-resumos", action="store_true",
                   help="ignora os resumos já gravados e gera de novo (o texto continua reaproveitado)")
    p.add_argument("--horas", type=float, help="sobrescreve a janela de tempo do config")
    p.add_argument("--todas", action="store_true", help="lista todas as notícias, sem separar por tema")
    p.add_argument("--json", metavar="ARQUIVO", help="salva notícias e temas em JSON")
    p.add_argument("--quieto", action="store_true", help="não lista as notícias, só o resumo")
    args = p.parse_args(argv)

    config = carregar_config(args.config)
    if args.horas:
        config.janela_horas = args.horas

    resultado = buscar_noticias(config)
    temas = carregar_temas(args.temas)
    paises = carregar_paises(args.paises) if Path(args.paises).exists() else []
    por_tema = classificar(resultado.noticias, temas, paises)
    avisos, banco, ja_enviadas = [], None, set()
    if not args.todas:
        avisos, banco, ja_enviadas = processar_temas(por_tema, args, config.timeout_segundos, config.janela_horas)

    if not args.quieto:
        if args.todas:
            imprimir_todas(resultado.noticias)
        else:
            imprimir_temas(por_tema)

    print("\n=== STATUS DOS FEEDS ===")
    for s in resultado.status:
        detalhe = s.erro if s.status == "erro" else f"{s.noticias} notícias"
        if s.descartadas:
            detalhe += f" ({s.descartadas} de fontes fora da lista)"
        nome = f"{s.fonte} ({s.categoria})"
        print(f"[{s.status.upper():5}] {nome:36} {detalhe}")

    ok = sum(s.status == "ok" for s in resultado.status)
    print(f"\nTotal: {len(resultado.noticias)} notícias de {ok}/{len(resultado.status)} feeds "
          f"(últimas {config.janela_horas:g}h)")
    for res in por_tema:
        print(f"Tema {res.tema.nome}: {len(res.noticias)} notícias")
    if not args.todas and args.painel:
        arquivo, qtd, av = gerar_painel(args.painel, temas, por_tema, banco, args.painel_dias)
        avisos += av
        print(f"Painel: {qtd} notícias em {arquivo}")
    if not args.todas and (args.enviar_email or args.salvar_email):
        avisos += processar_email(por_tema, args, banco, ja_enviadas)
    for aviso in avisos:
        print(f"AVISO: {aviso}")

    if args.json:
        dados = {
            "noticias": [n.to_dict() for n in resultado.noticias],
            "temas": [
                {
                    "id": r.tema.id,
                    "nome": r.tema.nome,
                    "noticias": [
                        {**m.noticia.to_dict(), "filtros": m.filtros, "palavras": m.palavras,
                         "relacionadas": [r.to_dict() for r in m.relacionadas],
                         "paises": m.paises, "resumo_ia": m.resumo_ia}
                        for m in r.noticias
                    ],
                }
                for r in por_tema
            ],
            "status": [s.__dict__ for s in resultado.status],
        }
        Path(args.json).write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Salvo em {args.json}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
