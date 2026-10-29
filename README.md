# Notícias

Script em Python que busca notícias nos feeds RSS dos principais jornais do
Brasil e do mundo, separa por temas e filtros, resume com IA e publica um
painel no GitHub Pages (o envio por e-mail continua disponível).

## Etapas

- [x] **1. Busca de notícias**: lê os feeds, limpa o texto, descarta notícias
      antigas e junta notícias repetidas entre jornais.
- [x] **2. Temas e filtros**: `temas.yaml` separa as notícias por tema.
      Primeiro tema: **Crédito** (spreads corporativos e soberanos, ratings,
      default, RJ/RE e renegociação de dívida).
- [x] **3. Resumo das notícias** com IA via OpenRouter (modelo escolhido por
      tarefa) + filtro de países (G10 e principais emergentes).
- [x] **Banco de dados (Supabase)**: guarda cada notícia com data, texto,
      resumo, link e tema, para consultar depois.
- [x] **4. E-mail pelo Gmail**: notícias do dia por tema e filtro, com link,
      fonte, horário (Brasília) e resumo.
- [x] **5. Agendado** 3x ao dia (06:53, 12:53 e 18:53, Brasília): o Supabase dispara a busca no GitHub (veja *Agendamento*);
      o agendamento do próprio GitHub fica como reserva.
- [x] **6. Painel no GitHub Pages**: página com as notícias dos últimos 30
      dias, busca, período, Brasil/Mundo e "só novas desde a última visita".
      O e-mail fica desligado (dá para religar, veja abaixo).

## Rodar

```bash
pip install -r requirements.txt
python -m n8news                     # notícias separadas por tema + status dos feeds
python -m n8news --todas             # todas as notícias, sem filtro de tema
python -m n8news --horas 6           # só as últimas 6 horas
python -m n8news --json noticias.json
python -m n8news --quieto            # só o status dos feeds
```

O GitHub Action **Buscar notícias** roda a busca a cada push (ou manualmente
em *Actions → Buscar notícias → Run workflow*) e salva o `noticias.json` como
artefato.

## Adicionar ou remover jornais

Edite `feeds.yaml`:

```yaml
- { fonte: TechCrunch, categoria: tecnologia, url: "https://techcrunch.com/feed/" }
```

Use `ativo: false` para desligar um feed sem apagar. Para jornais sem RSS
funcionando (caso do WSJ), dá para usar a busca do Google News como feed:
`https://news.google.com/rss/search?q=site:wsj.com+when:1d&hl=en-US&gl=US&ceid=US:en`.
Em `ignorar_links_com` ficam trechos de link a descartar (ex: `/patrocinado/`). O final da saída mostra o
**status de cada feed** (`OK`, `VAZIO` ou `ERRO`), útil para achar URLs que
pararam de funcionar.

## Temas e filtros

Edite `temas.yaml`. Cada tema tem filtros (ex: no tema Crédito: "Spreads
corporativos", "Default e calote", "Recuperação judicial"...). Uma notícia
entra no tema se passar em pelo menos um filtro.

```yaml
- nome: Recuperação judicial e extrajudicial
  ativo: true            # false desliga o filtro
  palavras:              # basta uma aparecer no título ou resumo
    - recuperação judicial
    - Chapter 11
  exige: []              # opcional: além disso, uma destas tem que aparecer
```

- **Adicionar/remover** um assunto: inclua ou apague linhas em `palavras`.
- `*` aceita qualquer final (`renegoci*` pega renegociação, renegociar...).
- Acentos, maiúsculas e hífens não importam.
- `excluir` (no tema) tira notícias com aquelas palavras (ex: Serasa).
- A saída mostra, em cada notícia, qual filtro e palavra a fizeram entrar,
  para facilitar ajustes.
- Notícias sobre o mesmo fato em vários jornais viram uma só, com
  "também em: ..." listando as outras fontes.

As buscas do Google News (categoria `credito` no `feeds.yaml`) só aceitam
notícias dos jornais em `google_news_fontes_permitidas`. Se quiser receber
de um site que está ficando de fora, adicione o nome dele nessa lista.

## Países

`paises.yaml` lista os países em grupos (`g10`, `em`, `outros`). No tema,
`paises_permitidos: [g10, em]` faz sair a notícia que cita **só** países de
fora (ex: Gana, Sri Lanka). Notícia que não cita país nenhum continua. Para
aceitar um país específico, adicione o código (ex: `[g10, em, VEN]`) ou mude
o grupo dele no `paises.yaml`.

## IA: relevância e resumo

Duas tarefas, cada uma com o modelo que você escolher (bloco `ia:` do
`temas.yaml`):

| Tarefa | O que faz | Padrão |
|---|---|---|
| `relevancia` | confirma se a notícia é mesmo do tema; as que não forem saem do e-mail | `anthropic/claude-haiku-5.5` (barato) |
| `resumo` | 1–2 frases em português por notícia | `anthropic/claude-opus-5.5` |

- Notícia **só com título** (sem texto nem trecho, comum no Google News) não
  passa pela IA de resumo: o resumo é o próprio título.
- **Opinião** (colunas, editoriais, análises assinadas) continua no e-mail,
  com o resumo começando por "Opinião — Fulano:". Quem marca é a tarefa de
  relevância.
- `--refazer-resumos` gera de novo os resumos de notícias já gravadas.

O acesso é pelo **OpenRouter** (um só cadastro para Claude, GPT, Gemini,
DeepSeek...). Para trocar o modelo, mude `modelo:` com o nome que aparece em
https://openrouter.ai/models. Um tema pode usar outro modelo só para ele
(`ia:` dentro do tema). Para ir direto na Anthropic, use `provedor: anthropic`
e nomes como `claude-opus-5-5`.

Chave:
- **GitHub Actions:** secret `OPENROUTER_API_KEY` (crie em
  https://openrouter.ai/settings/keys).
- **Local:** `export OPENROUTER_API_KEY=sk-or-...`

Sem a chave tudo funciona, só que com o trecho original do RSS no lugar do
resumo. `--sem-resumo` pula essa etapa; `--sem-texto` não baixa as matérias;
`--sem-banco` não lê nem grava no Supabase.

## Painel (GitHub Pages)

Cada execução gera `site/index.html` com as notícias do Supabase dos últimos
30 dias (`--painel-dias` muda) mais as da execução atual, e o GitHub publica
a página. O texto completo das matérias **não** vai para o painel (só título,
link, fonte, data e resumo).

Para ligar (uma vez):

1. *Settings → Pages → Build and deployment → Source*: **GitHub Actions**.
2. O endereço fica `https://<usuário>.github.io/<nome do repositório>/`.
   Para ser `.../noticias/`, renomeie o repositório para `noticias`
   (*Settings → General → Repository name*).
3. A publicação só acontece a partir do branch principal.

Atenção: no plano gratuito o GitHub Pages é **público** (qualquer pessoa com
o link vê; a página pede para não ser indexada por buscadores).

**Atualizar agora:** o botão no topo do painel abre o GitHub; clique em
*Run workflow* (precisa estar logado). O painel muda em 2 a 3 minutos.

**Uma notícia por assunto:** o painel junta num cartão só as notícias do
mesmo assunto (mesmo link, títulos parecidos ou a mesma empresa/país na mesma
seção em até 3 dias); as outras ficam em "Mais N matérias sobre o assunto".
No Supabase, se a mesma história volta com outro jornal como principal (mas
algum link do grupo já foi gravado nos últimos 3 dias), ela atualiza o
registro existente em vez de criar outro.

Localmente: `python -m n8news --painel site` e abra `site/index.html`.

## Agendamento (Supabase dispara o GitHub)

O agendamento do próprio GitHub atrasa ou pula horários. Por isso o relógio
principal é o Supabase: 3x ao dia (Brasília 06:53, 12:53 e 18:53) ele pede ao GitHub para rodar a busca. O agendamento do GitHub
continua como reserva e é pulado se já houve busca há menos de 90 minutos.

Para ligar (uma vez):

1. **Chave do GitHub** (só para disparar a busca):
   https://github.com/settings/personal-access-tokens/new
   - *Token name*: `noticias-supabase`; *Expiration*: o maior prazo que
     aparecer (anote a data: quando vencer, crie outra e repita o passo 2).
   - *Repository access*: **Only select repositories** → `noticias`.
   - *Permissions → Repository permissions → Actions*: **Read and write**.
   - *Generate token* e copie (começa com `github_pat_`).
2. **No Supabase**: *SQL Editor → New query*, cole o conteúdo de
   `supabase/migracoes/003_agendador.sql`, troque `COLE_A_CHAVE_AQUI` pela
   chave e clique em **Run**. Não salve a chave em nenhum outro lugar.
3. **Teste**: numa nova query, rode `select privado.disparar_busca_noticias();`
   e, depois de uns 5 segundos,
   `select status_code, content from net._http_response order by created desc limit 1;`.
   `204` = deu certo (a busca aparece em *Actions* no GitHub).

## E-mail (Gmail) — desligado, fácil de religar

Para voltar a receber por e-mail: crie os secrets do Gmail (abaixo) e a
variável `ENVIAR_EMAIL` = `true` em *Settings → Secrets and variables →
Actions → Variables*. O painel continua sendo publicado junto. Para desligar
de novo, apague a variável ou mude para `false`.

O e-mail sai pelo Gmail usando uma **senha de app** (não é a sua senha normal):

1. Ative a verificação em duas etapas na conta Google (obrigatório para
   senha de app): https://myaccount.google.com/signinoptions/twosv
2. Crie a senha de app em https://myaccount.google.com/apppasswords (nome:
   `n8news`). O Google mostra 16 letras; copie.
3. No GitHub, crie os secrets:
   - `GMAIL_USER`: seu endereço Gmail (remetente)
   - `GMAIL_APP_PASSWORD`: a senha de app (com ou sem espaços)
   - `EMAIL_PARA` (opcional): destinatários separados por vírgula; sem ele,
     o e-mail vai para o próprio `GMAIL_USER`.

**Ordem do e-mail:** segue a ordem dos filtros no `temas.yaml` (hoje:
Recuperação judicial/extrajudicial primeiro, depois Captações de empresas
brasileiras, Spreads, Soberanos, Ratings, Default, Renegociação). Dentro de
cada filtro, as notícias são separadas em **Brasil** e **Mundo** (desligue com
`separar_brasil: false` no filtro). Brasil = cita o Brasil ou uma empresa
brasileira, ou não cita país e está em português.

**Só notícias novas:** cada e-mail traz apenas notícias que ainda não foram
enviadas (ficam marcadas no Supabase). Funciona com um ou dois e-mails por dia.
Se não houver nada novo, o e-mail não é enviado. Requer rodar uma vez no
Supabase o arquivo `supabase/migracoes/002_envios.sql` (SQL Editor → Run).

O e-mail é enviado nos horários agendados ou ao rodar manualmente em
*Actions → Buscar notícias → Run workflow*; não a cada mudança no código.
Localmente: `python -m n8news --enviar-email` (ou `--salvar-email email.html`
para só gerar o arquivo e conferir).

## Banco de dados (Supabase)

Cada execução grava as notícias dos temas em duas tabelas:

- `noticias`: título, link, data de publicação, jornal(is), outros links da
  mesma notícia, países, trecho do RSS, **texto da matéria**, **resumo da IA**,
  data da coleta.
- `noticias_temas`: em qual tema cada notícia entrou e por qual filtro.

Notícia já gravada não é resumida de novo (economiza a API).

O texto da matéria é extraído da própria página. Em sites com paywall (Valor,
FT, WSJ) vem só o começo; links do Google News ficam só com o trecho do RSS.

**Configurar (uma vez):**
1. Crie um projeto em supabase.com.
2. *SQL Editor → New query*: cole o conteúdo de `supabase/schema.sql` e clique
   em *Run*.
3. *Project Settings → API*: copie a **Project URL** e a chave **secret**
   (ou `service_role`). Nunca use a chave pública/anon aqui.
4. No GitHub, crie os secrets `SUPABASE_URL` e `SUPABASE_KEY` (mesmo lugar do
   `ANTHROPIC_API_KEY`).

**Consultar:** no *Table Editor* do Supabase, ou no *SQL Editor*:

```sql
-- últimas notícias de crédito
select publicado_em, titulo, resumo_ia, link from noticias_por_tema
where tema = 'credito' limit 50;

-- busca por palavra (acentos e plural não importam)
select publicado_em, titulo, resumo_ia, link from noticias
where busca @@ websearch_to_tsquery('portuguese', 'Ri Happy')
order by publicado_em desc;
```

## O que cada notícia tem

`titulo`, `link`, `resumo` (trecho do próprio RSS, até 400 caracteres),
`fonte`, `categoria`, `publicado_em`, `imagem`, e `fontes`/`categorias`
quando a mesma notícia aparece em mais de um jornal.

## Testes

```bash
pip install pytest && python -m pytest
```
