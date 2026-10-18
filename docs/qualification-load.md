# Qualificação sintética: carga e migração preenchida

Este ensaio não executa operações em produção. O runner cria um banco novo com
nome aleatório, inicia suas próprias APIs em loopback e usa somente operadores,
clientes, agenda e catálogos sintéticos. Nunca recebe uma URL de API externa.
Nenhuma sessão WhatsApp, mensagem, integração financeira ou provedor de IA é
ativado. Os registros de canal são simulados, desabilitados e não abrem sockets.

## Contrato antes da medição

| Grandeza | Definição fixa |
|---|---|
| Usuário ativo | Sessão Redis própria, conta verificada sintética e membership owner de uma empresa |
| Crescimento | 10 → 100 → 500 → 1.000 → 1.500 sessões |
| Operação concentrada | 100 empresas; após cobrir todas, 40% dos operadores adicionais se concentram em cinco empresas |
| Distribuição ampla | 1.000 empresas, 1–2 operadores por empresa, 2.000 registros de canal desligados |
| Oferta | Uma jornada por usuário a cada 10 segundos; 1.500 usuários oferecem 150 jornadas/s, não 1.500 RPS |
| Chegadas | Relógio independente das respostas, início distribuído com semente; usuário ocupado ou limite de 300 jornadas pendentes gera descarte contado como erro |
| Mistura de jornadas | 10% produtos, 20% catálogo de serviços, 20% disponibilidade, 15% agenda paginada, 10% leitura de configuração, 10% edição de configuração com CAS/replay, 15% hold/replay/confirmação/cancelamento |
| Jornada versus HTTP | Uma jornada de escrita tem vários requests; o relatório separa oferta de jornadas e RPS HTTP realmente observado |
| Histórico | 10 agendamentos concluídos por empresa, catálogo de produtos e serviços, profissionais e expediente reais no PostgreSQL |
| Processos | Duas APIs uvicorn; pools reais da aplicação; nenhuma conexão permanente por usuário |
| Disputa | 20 solicitações concorrentes pela mesma vaga, três sementes independentes 17/29/43; um vencedor, demais conflitos explícitos |
| Isolamento | Todos os 1.500 operadores fazem uma leitura própria (200) e uma de outra empresa (403), por HTTP autenticado com Redis |

Metas imutáveis, gravadas no JSON **antes** de iniciar:

- Leituras usuais: p95 ≤ 800 ms; p99 ≤ 1.500 ms.
- Mutações usuais: p95 ≤ 2.000 ms; p99 ≤ 4.000 ms.
- Erros inesperados, incluindo chegadas descartadas: estritamente abaixo de 0,1%.
- Zero duplicidade de hold, sobreposição de reserva ativa, vínculos entre
  empresas, perda do histórico inicial, falso estado de confirmação/cancelamento
  ou ativação de jobs de envio.
- Conflitos 409 previstos são reportados e permanecem na distribuição de
  latências. Não são removidos para melhorar os percentis.

As latências do primeiro request incluem atraso do gerador desde a chegada
planejada. O scheduler não reduz a oferta quando as respostas ficam lentas.
Há limite de uma jornada simultânea por operador e de 300 jornadas em execução;
a oferta excedente permanece no denominador como erro. Timeout de request é
10 s. Não há retry automático de uma mutação de resultado desconhecido.

## Execução em CI isolado

Requisitos: Ubuntu, Python 3.12 com `pip install '.[test]'`, PostgreSQL 16,
clientes `pg_dump`/`pg_restore` 16 para a migração e Redis 7. Provisionar os três
papéis normais usando `infra/postgres/init/001-roles.sql` num serviço de CI novo.
Não usar variáveis ou credentials de produção.

Variáveis obrigatórias:

- `ORION_QUALIFICATION_DISPOSABLE=1`.
- `ORION_QUALIFICATION_OWNER_URL`: PostgreSQL/psycopg, usuário `postgres`, host
  `localhost`/`127.0.0.1`/`::1`, banco administrativo `postgres`.
- `APP_DATABASE_URL`, `MIGRATION_DATABASE_URL`, `SUPPORT_DATABASE_URL`: credenciais
  dos papéis `app_runtime`, `app_migrator`, `app_support` no **mesmo** host/porta.
  O runner substitui o nome do banco pelo que acabou de criar.
- `REDIS_URL=redis://localhost:6379/15`: DB15 deve estar vazio e dedicado ao job.
  Não compartilhar esse Redis entre dois runners concorrentes.

```sh
# Ensaio finito principal: ~15 min de medição, mais seed/migração/preflight.
python scripts/qualification_load.py --confirm-disposable-local \
  --profile concentrated --stage-seconds 60 --sustain-seconds 600 \
  --output artifacts/load-concentrated.json

# Verificação ampla curta em OUTRO job/serviço Redis, sem alegar sustentação.
python scripts/qualification_load.py --confirm-disposable-local \
  --profile wide --stage-seconds 5 --sustain-seconds 10 \
  --output artifacts/load-wide-smoke.json

# Harness sem serviços: testes de guardas, métricas e scheduler.
pytest -q tests/load/test_qualification_load.py
```

O primeiro comando faz 30 s de aquecimento, quatro estágios de 60 s, 600 s
sustentados em 1.500 sessões e 30 s de recuperação em dez sessões. Recomenda-se
timeout de job de 30 min. São possíveis `--sustain-seconds 1800` e `3600` para
preparar 30/60 minutos; sua existência **não significa execução concluída**.
A janela de 12 horas não foi cumprida e sempre aparece como pendente no JSON.
O comando curto se identifica como `short_smoke_only`.

O processo termina com código diferente de zero se uma meta, invariante,
preflight ou execução falhar. Não ajustar metas após observar o resultado.
Publicar os JSONs e `.api.log` como artefatos privados, inclusive quando falhar.
Eles contêm somente dados e identificadores sintéticos. O relatório não copia
senhas, URLs de conexão, cookies, CSRF ou corpos de erro HTTP.

## Observabilidade e interrupção

A cada cinco segundos registra conexões PostgreSQL, espera por locks, backlog
do outbox de calendário, memória Redis, RSS/CPU/descritores dos processos da API
no Linux e disco livre. Hardware, Python, configuração e duração ficam no JSON.
O ensaio aborta se APIs excederem 1.536 MiB de RSS (configurável dentro de limite
fixo), conexões do banco excederem 80 ou disco livre ficar abaixo de 512 MiB.
CPU é medida; este ensaio não modifica quotas nem escala a infraestrutura.

A limpeza termina apenas os processos iniciados pelo runner, remove apenas suas
chaves Redis (nunca `FLUSH`) e exige que nome, OID e proprietário do banco ainda
coincidam antes de removê-lo. Não usa `DROP ... FORCE` nem encerra sessões alheias.
Todas as URLs são recusadas fora de loopback; parâmetros de URL também são
recusados para impedir redirecionamento por configuração.

## Migração preenchida e retorno compatível

`tests/integration/test_qualification_populated_migration.py` cria bases locais
descartáveis, aplica 0019, preenche duas empresas com operadores, produtos,
serviços, expediente, clientes, histórico e job de calendário pendente. Captura
pg_dump real, cifra, lê de volta, confere SHA256, decifra e restaura em outro banco
**antes** de migrar a origem para o head Alembic corrente (inclui 0023 quando
presente). Compara todas as linhas/colunas antigas, grants, proprietários, RLS,
catálogo e estados inativos das funções novas. Exercita leitura RLS e INSERT
legado após o upgrade, mantendo a cópia antiga restaurada utilizável.

```sh
# Os serviços e papéis já devem existir; o teste cria seus próprios bancos.
export ORION_MIGRATION_TEST_OWNER_URL="$ORION_QUALIFICATION_OWNER_URL"
export ORION_REQUIRE_MIGRATION_OPS_TEST=1
pytest -q -s tests/integration/test_qualification_populated_migration.py
```

Esse teste não chama downgrade sobre registros novos, nem promete rollback de
schema seguro. A restauração é em outra base, e a origem mantém dados criados
depois do upgrade. Compatibilidade dos contratos SQL antigos não é execução de
um binário antigo de API/worker. O job pendente é preservado e legível; não há
sincronização externa nesse teste.

## Limites que permanecem explícitos

Não se qualifica capacidade de produção, mil sockets WhatsApp, áudio, modelos,
pagamentos, entrega externa, reinício/drenagem de worker, exportação pesada ou
12 horas de estabilidade. O perfil amplo curto qualifica montagem e isolamento,
não sustentação por 10 minutos. Os valores observados de p95/p99/erros só existem
após execução com serviços reais. Testes unitários do harness não substituem essa
execução; PG indisponível localmente é registrado como skip, nunca como PASS.
