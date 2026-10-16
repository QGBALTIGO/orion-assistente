# Arquitetura — FATIA 0

## Direção
API FastAPI leve e worker Arq usando Redis; fonte de verdade PostgreSQL 16.
Uma instância multiempresa atende vários estabelecimentos e cada contexto de
tenant será estabelecido exclusivamente no servidor após autenticação/validação
de evento externo (não implementados nesta fatia).

```text
         [Cliente futuro via canal autorizado]
                        |
          [FastAPI /health nesta fatia]
                        |
               [SQLAlchemy async]
                        |
       [PostgreSQL 16 + RLS + contexto local]
                        ^
  [Migrações/seed: papel separado app_migrator]

          [Redis] <---> [worker Arq]
```

## Banco
- `tenants`: id UUID, nome, slug, fuso e status. A API runtime somente lê
  o tenant que corresponde ao contexto; cadastro global ainda não existe.
- `tenant_probe`: tabela exclusivamente de teste do isolamento.
- Tabelas com tenant aplicam `ENABLE ROW LEVEL SECURITY` e `FORCE ROW LEVEL SECURITY`,
  com políticas específicas para app_runtime. Sem contexto, não retornam linhas.
- `set_config('app.tenant_id', uuid, true)` é local à transação;
  pool não deve reter identidade entre requests.
- `app_migrator` possui DDL e política de manutenção explícita; sua URL só
  existe nos jobs de migration/teste, nunca nos processos api/worker.
- O papel `postgres` inicializa roles e não opera a aplicação.

## Deploy
Docker Compose local: api, worker, postgres, redis, migrate/test opt-in e
Caddy HTTP de desenvolvimento opcional. Na Railway, serviços separados
com Postgres 16 e Redis; a borda da Railway fornece TLS e roteamento HTTP.

## Evolução sem antecipação
Modelos do catálogo, reservas, WhatsApp, integração Google e cobrança
não foram criados. A futura função de reservas será única, transacional,
com constraint anti-sobreposição PostgreSQL e idempotência.
