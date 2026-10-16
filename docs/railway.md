# Preparação de hospedagem na Railway (sem deploy automático)

A FATIA 0 inclui Dockerfile compatível com deploy containerizado. **Não foi
realizado login, compra, deploy, criação de projeto ou configuração de
credenciais na Railway.**

## Topologia prevista
- Serviço `api`: Dockerfile raiz; start padrão usa `PORT` da Railway;
  expor `/health`; definir somente `APP_DATABASE_URL` apontando para
  **app_runtime**, além de `REDIS_URL` se exigido futuramente.
- Serviço `worker`: mesmo repositório/Dockerfile; comando
  `arq app.worker.WorkerSettings`; definir `REDIS_URL`. Não expor domínio.
- Serviço PostgreSQL **versão 16**: confirmar a versão da imagem real no
  ambiente Railway antes de provisionar (não assumir versão padrão).
- Serviço Redis para Arq. Não abrir Redis/Postgres à internet desnecessariamente.
- Job administrativo *temporário* de migrations: imagem Docker, executar
  `alembic upgrade head` com **MIGRATION_DATABASE_URL** e sem URL runtime.
  Não armazenar a credencial de migrator no serviço API.

## Provisionamento
Criar banco e segredos pelo painel seguro da Railway. Com uma conexão de
admin autorizada, rodar `infra/postgres/init/001-roles.sql` via `psql`
fornecendo as variáveis `db_name`, `runtime_password` e
`migrator_password` pela ferramenta segura (ex.: job administrativo isolado).
**O script de inicialização local do Compose não roda automaticamente no
plugin PostgreSQL da Railway.** Configure os dois usuários antes de executar
Alembic e confirme grants/privileges e políticas RLS após cada migração.

As URLs privadas Railway podem aparecer como `postgresql://...`;
`APP_DATABASE_URL` aceita essa forma e adapta o driver asyncpg;
`MIGRATION_DATABASE_URL` adapta ao psycopg. URLs contendo senhas com
caracteres reservados precisam de codificação apropriada. Não copiar a
`DATABASE_URL` administrativa da Railway para API/worker.

## Portas, TLS e domínio
A borda Railway encaminha HTTPS para a porta `PORT` do contêiner FastAPI.
Caddy é **apenas local** no Compose e não integra o deploy Railway.
Nenhum domínio, preço, limite ou capacidade foi pressuposto.

## Antes do primeiro deploy público
Rodar CI real PostgreSQL 16, revisar variáveis, configurar backups e restore,
healthcheck e logs, autorizar custos/projeto/contas e aprovar as próximas fatias.
O botão de deploy não substitui a validação de isolamento nem a autorização.


## Adição na FATIA 1 (não implantada)
Provisionar papel `app_support` (não owner/não BYPASSRLS) via SQL de init
executado por administrador Railway **uma única vez**.
API recebe `SUPPORT_DATABASE_URL` (somente app_support) e
`SUPPORT_API_TOKEN` aleatório de alta entropia; worker não recebe
essas credenciais. `VIEWER_API_TOKEN` e `VIEWER_TENANT_ID` opcionais.
Não ativar domínio público/painel operacional até configurar regras de rede,
rotação de token, rate limiting e revisão de permissões/backup.
