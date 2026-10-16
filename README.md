# Orion Barbearias — FATIAS 0, 1 e 2

Fundação de um SaaS gerenciado de agendamento por WhatsApp para barbearias.
**Status: implementação de fundação, não pronta para clientes reais.**
Escopo estritamente limitado à **FATIA 0** do Prompt Mestre (seção 19).
Não há agenda, login, bot, integrações externas, pagamentos ou landing page nesta entrega.

## Stack
Python 3.12, FastAPI, SQLAlchemy async + asyncpg, Alembic + psycopg,
PostgreSQL **16**, Redis 7, Arq e Docker Compose. A API publica somente
`GET /health` → `{"status":"ok"}`.

## Desenvolvimento local com Docker

1. `cp .env.example .env`.
2. Preencha os três campos de senha com valores aleatórios e **diferentes**,
   nunca com senhas de contas pessoais. Para gerar valores seguros:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Repita para `POSTGRES_PASSWORD`, `APP_RUNTIME_PASSWORD` e
   `APP_MIGRATOR_PASSWORD`. Não versionar `.env`. Evite caracteres
   reservados de URL em senhas embutidas nas URLs de conexão; token_urlsafe
   usa caracteres compatíveis.

3. Inicialize banco e fila:

   ```bash
   docker compose up -d postgres redis
   docker compose --profile tools run --rm --build migrate
   docker compose up -d --build api worker
   curl -i http://127.0.0.1:8000/health
   ```

4. Execute testes **reais** contra o mesmo PostgreSQL 16:

   ```bash
   docker compose --profile tools run --rm --build test
   ```

   O serviço `test` exige integração real (`REQUIRE_POSTGRES_TESTS=1`),
   portanto não aceita testes de RLS silenciosamente ignorados.

5. Opcional: proxy Caddy de desenvolvimento (HTTP na porta 8080):

   ```bash
   docker compose --profile proxy up -d caddy
   curl -i http://127.0.0.1:8080/health
   ```

   A Railway termina HTTPS em produção; **não** usar esse Caddy HTTP
   como proxy público de produção.

6. Operação e limpeza:

   ```bash
   docker compose logs --tail=100 api worker postgres redis
   docker compose down
   ```

   `docker compose down -v` **apaga permanentemente** o volume local do banco:
   usar somente em ambiente descartável.

**Importante:** o script de criação de papéis roda apenas no primeiro initdb.
Se um volume já existia, revise/provisione os papéis manualmente: o Compose
não recria permissões automaticamente.

## Testes sem Docker
Instale `pip install ".[test]"` em Python 3.12 e rode `pytest -q -ra`.
Os unitários rodam sem serviços; os testes de integração exigem
`APP_DATABASE_URL` e `MIGRATION_DATABASE_URL` com PostgreSQL migrado.
Defina `REQUIRE_POSTGRES_TESTS=1` para fazer falhar em vez de pular a
integração quando variáveis faltarem.

## Deploy Railway (futuro, sem executar nesta FATIA 0)

Consulte [docs/railway.md](docs/railway.md) para configuração de API/worker,
URLs de conexão separadas, provisionamento de papéis e pré-requisitos.
Nenhuma credencial, conta Railway ou domínio foi conectado por esta mudança.

## Documentação
- [Arquitetura](docs/architecture.md)
- [Segurança e isolamento](docs/security.md)
- [Operação](docs/runbook.md)
- [Railway](docs/railway.md)
- [Roadmap](docs/backlog.md)

**Aceite:** migrations passam e todos os testes de isolamento rodam sobre
PostgreSQL real. Sem esse resultado, a FATIA 0 permanece não validada.


## FATIA 1: catálogo interno e operação autorizada

Após migrations, acesse `http://127.0.0.1:8000/panel`. A interface em
português permite cadastrar barbearia, equipe autorizada, profissionais,
serviços, vínculo de serviços/profissionais, expediente, pausas, folgas e
bloqueios. A interface usa um token de suporte forte, informado em tempo de
execução, sem salvar em localStorage. **Não compartilhe token ou senha por chat.**

Configure mais duas credenciais novas e distintas no `.env`:
`APP_SUPPORT_PASSWORD` (papel separado, sem BYPASSRLS) e
`SUPPORT_API_TOKEN` (gerado com `secrets.token_urlsafe(48)`).
`VIEWER_API_TOKEN` e `VIEWER_TENANT_ID` são opcionais para consulta
de um único estabelecimento; leitores não fazem alterações.

- `POST /api/v1/tenants` e `GET /api/v1/tenants`: somente suporte.
- `GET/POST /api/v1/tenants/{id}/{resource}`: consulta/cadastro.
- `PUT/DELETE /api/v1/tenants/{id}/{resource}/{item_id}`: edição e remoção.
- `GET /api/v1/tenants/{id}/audit`: trilha de operações.
- Todos exigem header `Authorization: Bearer <token>`.
- O sistema não aceita `tenant_id` arbitrário em itens: suporte pode
  escolher a empresa no painel após autenticação; leitor só usa seu escopo.
- Os preços são inteiros em centavos. Cada dia do profissional admite um
  intervalo principal de expediente e pausas internas.
- Horários, folgas e bloqueios **não** criam reservas nesta fatia.

Para verificar o isolamento real, rode
`docker compose --profile tools run --rm --build test`.
**Importante:** bancos antigos da FATIA 0 não ganham o novo papel
`app_support` automaticamente: aprovisionar o papel explicitamente em
ambiente já existente ANTES de executar `alembic upgrade head`.
Não apague volumes de dados para aplicar migração.

### Atualização segura de um banco local vindo da FATIA 0

**Se o volume PostgreSQL já existia**, execute este comando após
preencher APP_SUPPORT_PASSWORD, antes de aplicar a migration da FATIA 1:

```bash
docker compose up -d --wait postgres
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v db_name="$POSTGRES_DB" -v support_password="$APP_SUPPORT_PASSWORD" -f /etc/orion/upgrade-support.sql'
docker compose --profile tools run --rm --build migrate
```

O procedimento é idempotente e não elimina dados. Não use `down -v` como
forma de atualização de uma instalação com informações persistentes.

## FATIA 2 — Agenda interna

Consulte [docs/booking-engine.md](docs/booking-engine.md) para rotas de disponibilidade,
holds, confirmações, cancelamento, remarcação, isolamento e testes de concorrência.
A aplicação ainda NÃO recebe mensagens WhatsApp nem permite agendamento pelo cliente final.
