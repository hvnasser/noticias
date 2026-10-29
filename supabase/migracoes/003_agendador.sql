-- Agendador da busca de notícias pelo Supabase (pg_cron + pg_net)
--
-- O agendamento do GitHub Actions é "quando der" e às vezes pula horários.
-- Aqui o próprio Supabase chama o GitHub no horário certo para rodar o
-- workflow "Buscar notícias".
--
-- Como usar (SQL Editor do Supabase):
--   1. Troque COLE_A_CHAVE_AQUI (aparece UMA vez, no passo 2) pela chave do
--      GitHub. Não salve a chave neste arquivo nem no repositório.
--   2. Clique em Run. Pode rodar de novo quando quiser trocar a chave.
--   3. Teste: veja o final do arquivo.

-- 1) Extensões (agendador e chamadas HTTP)
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- 2) Guarda a chave no cofre do Supabase (Vault), fora das tabelas
do $$
declare
  v_chave text := 'COLE_A_CHAVE_AQUI';
  v_id uuid;
begin
  if v_chave = 'COLE_A_CHAVE_AQUI' then
    raise exception 'Troque COLE_A_CHAVE_AQUI pela chave do GitHub antes de rodar';
  end if;
  select id into v_id from vault.secrets where name = 'github_token_noticias';
  if v_id is null then
    perform vault.create_secret(v_chave, 'github_token_noticias', 'GitHub: disparar a busca de notícias');
  else
    perform vault.update_secret(v_id, v_chave);
  end if;
end $$;

-- 3) Função que pede ao GitHub para rodar a busca. Fica num schema que a API
--    pública do Supabase não expõe.
create schema if not exists privado;
revoke all on schema privado from public, anon, authenticated;

create or replace function privado.disparar_busca_noticias() returns bigint
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_token text;
begin
  select decrypted_secret into v_token from vault.decrypted_secrets where name = 'github_token_noticias';
  if v_token is null then
    raise exception 'Chave do GitHub não cadastrada (rode o passo 2)';
  end if;
  return net.http_post(
    url := 'https://api.github.com/repos/hvnasser/noticias/actions/workflows/buscar-noticias.yml/dispatches',
    body := jsonb_build_object('ref', 'claude/n8n-rss-news-email-ody260'),
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || v_token,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'noticias-supabase',
      'Content-Type', 'application/json'
    )
  );
end $$;

revoke all on function privado.disparar_busca_noticias() from public, anon, authenticated;

-- 4) Agenda 3x ao dia (o horário do pg_cron é UTC; Brasília = UTC-3):
--    Brasília 06:53, 12:53 e 18:53
select cron.unschedule(jobid) from cron.job where jobname = 'buscar-noticias';
select cron.schedule('buscar-noticias', '53 9,15,21 * * *', 'select privado.disparar_busca_noticias()');

-- 5) Teste (opcional): dispara agora e confere a resposta do GitHub.
--    Rode esta linha:
--      select privado.disparar_busca_noticias();
--    Espere uns 5 segundos e rode:
--      select status_code, content from net._http_response order by created desc limit 1;
--    status_code 204 = deu certo (a busca aparece em Actions no GitHub).
--    401 = chave errada ou vencida; 403/404 = chave sem a permissão "Actions: Read and write"
--    neste repositório.
--
-- Para ver as últimas execuções do agendador:
--   select start_time, status, return_message from cron.job_run_details
--   where jobid = (select jobid from cron.job where jobname = 'buscar-noticias')
--   order by start_time desc limit 10;
--
-- Para desligar:
--   select cron.unschedule('buscar-noticias');
