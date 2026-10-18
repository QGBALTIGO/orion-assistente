# Orion Barbearias — código das FATIAS 0–8

Fundação de um SaaS gerenciado de agendamento por WhatsApp para barbearias.
**Status: desenvolvimento e testes automatizados; não liberado para clientes reais.**
O histórico contém PRs incrementais das FATIAS 0–8. Integrações e módulos
comerciais avançados permanecem desabilitados ou incompletos sem piloto,
credenciais oficiais, revisão de segurança e autorizações dos titulares.

## Stack
Python 3.12, FastAPI, SQLAlchemy async + asyncpg, Alembic + psycopg,
PostgreSQL **16**, Redis 7, Arq e Docker Compose. A API publica `GET /health` e rotas documentadas no código.
A agenda interna permanece a autoridade de reservas.

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

## Homologação Railway implantada (2026-10-16)

Consulte [docs/railway-homologacao.md](docs/railway-homologacao.md) para a
configuração implantada de API/worker, papéis separados, migrations e
limitações de segurança. Domínio de testes:
https://orion-assistente-production.up.railway.app

Barbearia sintética de homologação: tenant_id
`82552b25-d0c8-5338-8c42-49f11de59e45`. Administração: `/panel`,
utilizando o `SUPPORT_API_TOKEN` guardado nas variáveis do serviço web
na Railway. Não transmitir token por links, screenshots ou mensagens.
Nenhuma credencial real de integrações externas está ativa.

Referência histórica: [docs/railway.md](docs/railway.md). foi conectado por esta mudança.

## Documentação
- [Gestão Orion: referência BarberPilot, medidas e implementação](docs/barberpilot-orion.md)
- [Cobertura funcional por módulo](docs/barberpilot-functional-coverage.md)
- [Migrations, testes e implantação da suíte de gestão](docs/management-suite-runbook.md)
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

### Upgrade de um volume PostgreSQL existente: FATIA 2

O banco anterior pode não ter o grant necessário para a extensão btree_gist.
Atualize o papel de migrations sem apagar o banco, antes de aplicar Alembic:

```bash
docker compose up -d --wait postgres
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v db_name="$POSTGRES_DB" -f /etc/orion/upgrade-booking.sql'
docker compose --profile tools run --rm --build migrate
docker compose --profile tools run --rm --build test
```

Em Railway, um administrador autorizado aplica o mesmo GRANT ao usuário
app_migrator pelo painel/console seguro; não use a credencial migrator na API.
Não fazer deploy externo sem autorização. Não usar down -v para atualizar.

## FATIA 3 — Webhook Meta e menus

Webhook autenticado, deduplicação durável, Redis/Arq e provedor Fake.
Instruções e limitações em [docs/whatsapp.md](docs/whatsapp.md).
Sem conta Meta autorizada, o envio externo é mantido desabilitado.

## FATIA 4 — Google Calendar opcional e exportação CSV

O motor interno continua sendo autoridade de agendamento. Google usa OAuth por empresa,
refresh token criptografado e outbox com retry (nenhum evento real sem opt-in).
A exportação CSV UTF-8 com BOM evita fórmulas do Excel e exige autorização da empresa.
Consulte [docs/google-calendar.md](docs/google-calendar.md).

## FATIA 5 — Preparação do piloto

O teste de restauração pg_dump/pg_restore roda em banco efêmero no CI.
Para checklist de pré-requisitos, execute `python scripts/pilot_preflight.py`.
Consulte [docs/pilot-beta.md](docs/pilot-beta.md). **Piloto real ainda não autorizado nem executado.**

## FATIA 6 — Landing e Asaas sandbox

Landing original em `/` sem CTA ativo enquanto SALES_WHATSAPP_E164 não estiver definido.
A entrada Asaas `/webhooks/asaas` aceita somente eventos autenticados com token exclusivo de webhook.
Nenhum pagamento é criado ou cobrado pelo sistema nesta prévia.
Mais informações: [docs/commercial.md](docs/commercial.md) e [docs/pricing-costs.md](docs/pricing-costs.md).

## FATIA 7 — Teste sintético de quinze empresas

A suíte de integração insere 15 tenants sintéticos e verifica em paralelo isolamento
RLS e canais distintos. Consulte [docs/capacity.md](docs/capacity.md). Sem piloto
real ou testes de carga em Railway, capacidade para quinze NÃO está comprovada.

## FATIA 8 — Estruturas avançadas protegidas

Tabelas multiempresa RLS para handoff humano, consentimento explícito,
campanhas em rascunho e workflows desabilitados. Rotas internas
requerem operador autenticado. O envio de campanhas retorna 423,
a IA não pode alterar tenant nem reservar por ferramenta livre, e o
transcritor de testes é explicitamente fictício.

**Não é plataforma BotBot pronta**, nem integra Chatwoot, LLM,
transcrição real, visualizador React Flow ou onboarding autônomo.
Essas integrações requerem piloto real, orçamento aprovado, revisão de
licenças e testes específicos. Veja [docs/advanced.md](docs/advanced.md).

## Teste online da agenda

1. Abra https://orion-assistente-production.up.railway.app/ (landing) e
   https://orion-assistente-production.up.railway.app/panel (painel).
2. Localize o `SUPPORT_API_TOKEN` nas variáveis privadas do serviço
   `orion-assistente` no projeto Railway `strong-creation`.
3. Insira esse token **apenas no painel HTTPS**. Selecione a barbearia
   com nome DEMONSTRAÇÃO FICTÍCIA. Há dois profissionais, três serviços,
   expedientes de segunda a sábado; o WhatsApp externo está desativado.
4. A API interativa em `/docs` permite consultar disponibilidade,
   criar holds, confirmar, cancelar e remarcar com token Bearer. Use somente
   clientes e números **fictícios**, nunca clientes de verdade.

API e worker estão no ambiente chamado `production` pela Railway,
mas este é um **ambiente de testes não homologado para dados reais**.

