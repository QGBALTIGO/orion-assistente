# Runbook — desenvolvimento / homologação

## Subir
`cp .env.example .env`, definir as três senhas distintas, e executar:

```bash
docker compose up -d postgres redis
docker compose --profile tools run --rm --build migrate
docker compose up -d --build api worker
docker compose --profile tools run --rm --build test
```

`GET http://127.0.0.1:8000/health` deve responder status ok.

## Diagnóstico
- `docker compose ps` para serviços e healthchecks.
- `docker compose logs --tail=100 api worker postgres redis` para falhas.
- Se migrations falharem: verificar se `app_migrator` foi provisionado,
  se o banco é PostgreSQL 16 e se `MIGRATION_DATABASE_URL` está correta.
- Se integrações falharem: `REQUIRE_POSTGRES_TESTS=1` impede false positives.
- Nunca inserir URLs com senhas em tickets ou logs.

## Operações futuras antes de produção
Backups cifrados externos e testes de restauração, rotação de segredos,
observabilidade, proteção de endpoints administrativos e plano de contingência
são pendências. Não promover ao piloto sem aprovar as fatias respectivas.

## Upgrade preservando volume da FATIA 0

Se app_support não existia quando o volume foi criado, use o script
idempotente detalhado no README. Depois aplique a migration normalmente.
Nunca apague volumes persistentes para atualizar a instalação.
