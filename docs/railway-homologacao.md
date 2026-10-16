# Orion — Deploy de homologação na Railway

Infra da conta conectada: projeto strong-creation, PostgreSQL 16, Redis e
serviço web já vinculado ao GitHub, branch main.

### Topologia

- \`orion-assistente\`: API FastAPI/landing via Dockerfile, HTTPS em domínio Railway;
  \`APP_DATABASE_URL\` credencial exclusiva app_runtime (RLS FORCE),
  \`SUPPORT_DATABASE_URL\` exclusiva app_support, token de operador
  \`SUPPORT_API_TOKEN\` (mínimo 32 caracteres). Start Uvicorn na porta PORT.
- \`orion-worker\`: mesmo Dockerfile, comando \`arq app.worker.WorkerSettings\`;
  \`REDIS_URL\` privado mais URLs runtime/support; **sem domínio público**.
- \`Postgres\` versão 16 e \`Redis\`, apenas via rede privada.
- \`orion-bootstrap\`: job isolado de execução eventual do
  \`python -m scripts.railway_bootstrap\`; recebe \`DATABASE_URL\` administrativo,
  e \`APP_RUNTIME_PASSWORD\`, \`APP_MIGRATOR_PASSWORD\`,
  \`APP_SUPPORT_PASSWORD\`, todas distintas e com 32 caracteres. Somente
  este job recebe credencial administrativa; revogar/limitar ao terminar.

### Ordem de implantação

1. Provisionar Postgres 16, Redis e segredos do ambiente.
2. Rodar o bootstrap de papéis e migrations Alembic. Não prosseguir sem
   verificar RLS fail-closed.
3. Configurar as conexões API/worker referenciando host privado, database
   e senhas correspondentes. Confirmar que API e worker não recebem
   \`DATABASE_URL\` de administrador.
4. Publicar API e confirmar \`GET /health\` responde status ok.
5. Conferir \`GET /api/v1/ops/status\` com Bearer autorizado e monitorar logs.
6. Cadastros via \`/panel\` (token operacional em memória, não persistido no
   navegador). Nunca enviar token em URL nem capturas de tela.

### Segurança

\`META_LIVE_SEND=0\` e \`GOOGLE_LIVE_SYNC=0\` no piloto. Nenhum token
Meta/Google/Asaas real deve entrar sem consentimento/verificação do titular.
O domínio público não equivale a liberação comercial. Trocar qualquer senha
compartilhada anteriormente, aplicar backup offsite/restore e revisitar
autenticação por usuários e limites de requisições antes de clientes reais.

Migrações e segredos do banco não podem ser executados com credencial
da aplicação. Não mover o script de bootstrap para o start da API.
