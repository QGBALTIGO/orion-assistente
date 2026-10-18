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
| Processos | Três workers uvicorn; por papel/worker, pool 5 + overflow 5; teto teórico 60 conexões de API; nenhuma conexão permanente por usuário |
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
  --api-workers 3 --db-pool-size 5 --db-max-overflow 5 \
  --output artifacts/load-concentrated.json

# Ensaio amplo principal em OUTRO job/serviço Redis, também completo.
python scripts/qualification_load.py --confirm-disposable-local \
  --profile wide --stage-seconds 60 --sustain-seconds 600 \
  --api-workers 3 --db-pool-size 5 --db-max-overflow 5 \
  --output artifacts/load-wide.json

# Harness sem serviços: testes de guardas, métricas e scheduler.
pytest -q tests/load/test_qualification_load.py
```

Cada perfil principal faz 30 s de aquecimento, quatro estágios de 60 s, 600 s
sustentados em 1.500 sessões e 30 s de recuperação em dez sessões. Recomenda-se
timeout de job de 30 min. São possíveis `--sustain-seconds 1800` e `3600` para
preparar 30/60 minutos; sua existência **não significa execução concluída**.
A janela de 12 horas não foi cumprida e sempre aparece como pendente no JSON.
Execuções curtas ficam restritas ao diagnóstico descrito adiante e se
identificam como `short_smoke_only`; não substituem esses dois perfis completos.

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
12 horas de estabilidade. Perfis curtos de diagnóstico verificam montagem e
isolamento, não sustentação por 10 minutos. Os valores observados de p95/p99/erros só existem
após execução com serviços reais. Testes unitários do harness não substituem essa
execução; PG indisponível localmente é registrado como skip, nunca como PASS.

## Diagnóstico após a primeira carga real

As primeiras cargas completas com dois workers reprovaram em 1.000 e 1.500
sessões. Os resultados permanecem preservados. O próximo candidato usa três
workers, com `ORION_DB_POOL_SIZE=5` e `ORION_DB_MAX_OVERFLOW=5` tanto no engine
runtime quanto no support: o teto teórico da API permanece em 60 conexões, como
nos dois workers anteriores com defaults 5 + 10. Worker de tarefas, migrador e
monitoramento usam conexões adicionais; o limite medido global continua em 80.
O gerador passa esses valores
explicitamente ao servidor e os registra antes da medição. Sua configuração
ambiente não é herdada. A publicação precisa usar o mesmo `WEB_CONCURRENCY=3`
e os dois limites de pool; aumentar processos não comprova aprovação.
Continuam iguais a oferta, os timeouts, as metas, a contagem dos descartes e
os limites medidos de 80 conexões PostgreSQL e 1.536 MiB. O custo adicional de
memória da normalização do estado será observado nessa mesma carga completa.

A coleta opcional `--profile-api` inicia a mesma aplicação em um wrapper de
lifespan exclusivo do ensaio local descartável. Cada worker salva seus próprios
arquivos `.prof` e JSON com funções/chamadas/tempos, sem argumentos ou conteúdo
das requisições. Usa relógio de CPU do processo; tempo acumulado de corrotinas
não deve ser somado nem interpretado como CPU exclusiva. O resultado se identifica
como `cpu_profile_diagnostic` e nunca qualifica capacidade. As metas e o código
de saída da carga continuam preservados.

O workflow oferece essa coleta somente por execução manual, com a opção
`cpu_profile`; as cargas principais 60/600 e as vinte jornadas de navegador
continuam obrigatórias nas PRs. Os perfis curtos servem para escolher uma
correção, e seus artefatos não substituem a carga completa sem instrumentação.

O gerador versão 2 distribui 40% dos operadores adicionais pelas cinco empresas
mais movimentadas, com contagem por empresa no relatório. A versão anterior
escolhia somente duas dessas cinco empresas por um erro no índice. Os relatórios
anteriores permanecem preservados e identificados; não representam o novo perfil.

O relatório versão 2 separa `invariants.data_integrity` de
`invariants.observation_uncertainty`. Uma resposta de hold perdida pode deixar
uma linha válida no banco: isso não prova duplicação. A reconciliação consulta
as linhas sintéticas e compara as chaves registradas **antes** da tentativa,
os IDs realmente recebidos e os cancelamentos efetivamente reconhecidos.
Linhas sem tentativa registrada, IDs recebidos ausentes e cancelamentos
reconhecidos que não persistiram continuam sendo violações de integridade.
Hold gravado sem resposta, jornada não encerrada e cancelamento não observado
são incerteza de observação. **Ambas as categorias bloqueiam o aceite.**
Nenhuma reconciliação reenvia ou cancela operações, nem remove timeouts,
conflitos inesperados ou chegadas descartadas do denominador.

Após a recuperação, o processo sintético da API é encerrado graciosamente
antes da consulta final: 15 segundos para concluir requisições em andamento e
até 30 segundos para o supervisor terminar. Falha nessa drenagem bloqueia o
resultado. Isso evita ler o banco enquanto uma escrita cuja resposta expirou
ainda pode concluir. O timeout HTTP de 10 segundos durante a carga permanece.
Cada usuário possui um profissional próprio, mas repete o mesmo horário após
cancelar; se uma operação ficar incerta, eventual conflito na jornada seguinte
continua sendo falha, não é reclassificado como contenção esperada.

As fases `warmup`, `measured` e `recovery` agora têm namespaces idempotentes
distintos. O replay deliberado dentro da mesma jornada mantém a mesma chave.
Isso evita pedir na recuperação o replay de uma reserva já cancelada no
aquecimento. Os relatórios anteriores são preservados; seus problemas de
latência e descarte não são anulados por essa correção do oráculo.

Para comparar o processo da API com 2, 3 ou 4 workers, em jobs isolados com os
mesmos serviços/flags e credenciais sintéticas descritos acima:

```sh
python scripts/qualification_load.py --confirm-disposable-local \
  --profile wide --stage-seconds 60 --sustain-seconds 120 \
  --api-workers 3 --output artifacts/load-wide-workers3-probe.json
```

Repetir com `--api-workers 2` e `4`, mudando apenas o nome do artefato. Esse
perfil é `short_smoke_only`: serve para diagnóstico comparável, não substitui
a sustentação de 600 segundos. A cadência continua 10 segundos por usuário
(150 jornadas/s em 1.500), timeout HTTP continua 10 segundos, fila máxima 300,
metas continuam leituras 800/1.500 ms e escritas 2.000/4.000 ms p95/p99. O
limite de 80 conexões PostgreSQL e 1.536 MiB também não foi aumentado; se mais
workers ultrapassarem esses limites, o diagnóstico deve falhar e registrar o
motivo. Mais workers não representam, por si só, correção do gargalo.
