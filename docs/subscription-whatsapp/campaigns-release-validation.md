# Etapa 07 — publicação e validação de catálogo

Estado: em qualificação. A migração e os 338 testes de PostgreSQL real passaram
na revisão intermediária 427b0cff; a revisão integrada à produção 01d99bb ainda
precisa de seus próprios resultados finais. Nenhuma operação de produção, envio,
teste de proxy ou agendamento real foi executado por esta integração.

## Cadeia de publicação

A revisão única `0068_subscription_campaigns` sucede
`0067_subscription_diagnostics`, que preserva `0066_appointment_operations`.
O predeploy existente mantém todos os checks anteriores e executa Campanhas
depois de Agenda e Diagnósticos.

`scripts/backup_and_migrate_subscription_campaigns.py` aceita somente a origem
0067 revisada. Antes de executar Alembic, verifica o catálogo anterior e cria
um arquivo privado com ownership e ACLs preservados. A rotina compartilhada
verifica o formato do arquivo, tamanho e SHA-256 dos metadados remotos e faz
leitura integral do objeto para conferir novamente tamanho e hash. O prefixo
é `production/subscription-campaigns-0068/pre-schema-0067_subscription_diagnostics-`.
Falha em qualquer etapa impede DDL. Depois do backup, a revisão e o catálogo
de origem são rechecados; uma alteração concorrente não é sobrescrita.

Um processo Alembic com saída zero só aprova a release quando a revisão 0068
e seu catálogo completo são confirmados. Reiniciar com a revisão já publicada
faz apenas a verificação. As allowlists das releases anteriores reconhecem
0068 sem repetir backups, migrações ou concessões de acesso. Os logs da nova
release usam marcadores finitos e não imprimem erros internos, credenciais ou
conteúdo do banco.

## Catálogo obrigatório

O validador é independente do código da migração e incorpora os contratos
publicados anteriores. Ele exige as sete tabelas privadas de grupos, membros,
permissões, campanhas, fontes congeladas, destinatários e pedidos, verificando:

- Tipos, tamanhos e nulabilidade das colunas do contrato.
- PKs, chaves de deduplicação/idempotência e FKs com o tenant, geração do
  dispositivo e vínculo campanha–destinatário–parte corretos.
- Checks de revisão, snapshot, estados, limites, autorização e prévia de
  120 segundos; conservar somente o nome de um check não basta.
- Índices válidos e prontos, colunas na ordem esperada e predicados dos
  seletores de pendências e da janela de admissão.
- Ownership `app_migrator`, RLS habilitada e forçada, políticas esperadas
  e ausência de grants públicos inclusive em colunas.
- `app_runtime` sem leitura ou escrita do conteúdo privado. `app_support`
  recebe SELECT/INSERT e somente as colunas UPDATE previstas para cada
  tabela; as fontes congeladas não recebem UPDATE. Os grants preexistentes
  do broker em `subscription_device_effects` permanecem preservados.

Não se adicionam funções `SECURITY DEFINER` nem acesso do runtime ao ledger
privado. A consulta de readiness lê somente catálogos PostgreSQL.

## Startup da API e do worker

Antes de abrir Uvicorn, a entrada normal de `scripts/start_api.py` executa a
barreira 0068. Quando o ambiente inicial contém credenciais ou flags de migração,
o launcher primeiro se reexecuta com essas chaves removidas; assim o ambiente
inicial do processo pai também fica limpo antes de criar o filho da barreira.
A segunda entrada não contém as chaves e não repete a reexecução.

O modo SSH publicado em 01d99bb mantém sua fronteira de `execve` diretamente
para `scripts.reverse_ingress`. Depois dessa limpeza e da validação da
configuração, a entrada do supervisor chama a mesma barreira com ambiente
também sem as três chaves SSH. A supervisão da API e do túnel só começa depois
do sucesso; erro, timeout ou falha ao criar o processo interrompe a inicialização
com um marcador finito. Argumentos de rede, pinning, workers e lifecycle do
supervisor permanecem como publicados.

A barreira inclui os contratos
anteriores, exige identidade autenticada `app_runtime`, conexão somente leitura,
`search_path=pg_catalog`, tentativas de até três segundos e prazo total de
90 segundos; o launcher limita o subprocesso a 95 segundos. Falha ou timeout
impede que a API comece a atender.

A cadeia `wait_for_single_whatsapp_schema` aguarda Campanhas depois de
Diagnósticos. O hook `WorkerSettings.on_startup` também aguarda 0068 antes de
definir `plan_permissions_ready` e antes do ponto
`orion_plan_permissions_0050_worker_ready`. As barreiras Agenda e Diagnósticos
continuam na ordem anterior. Nenhuma dessas rotas de startup executa migração
ou admite mensagens.

## Evidência local

Em 04/11/2026, **311 testes unitários passaram, sem falhas ou skips, em 2,07 s**.
O conjunto cobre a release nova, a cadeia das releases anteriores, sanitização
do launcher da API e barreiras de predeploy/ARQ. Inclui execução da rotina real
de backup com subprocessos e armazenamento sintéticos: arquivo inválido,
upload recusado, metadados incorretos e conteúdo remoto divergente impedem DDL;
o stream remoto é encerrado mesmo quando o checksum falha.

Depois de incorporar o commit de produção 01d99bb, passaram outros **115 testes
focados em 0,79 s**, com `test_start_api.py`, `test_reverse_ingress.py`,
`test_reverse_proxy_contract.py` e `test_subscription_campaigns_release.py`.
Eles verificam a fronteira limpa antes do probe, a ausência de SSH no ambiente
do probe e a interrupção antes de servir quando ele falha. O gate Python do
CI passa a incluir também os dois arquivos de testes de ingress.

Na atualização seguinte da produção, `a2f84ea`, foram incorporados os nove
arquivos do transporte HTTPS opcional, incluindo os hashes verificados do
download da imagem. A composição preservou as funções publicadas e o probe
somente leitura em `main`. As duas variáveis HTTPS passaram a integrar a mesma
exclusão de ambiente já aplicada às três de SSH. **158 testes focados passaram
em 0,91 s**, sem falhas ou skips, acrescentando `test_https_transport.py` aos
quatro arquivos da verificação anterior. Esse arquivo também integra o gate
Python e seus paths de disparo.

Comando executado no ambiente Python de testes:

```sh
python -m pytest -q -ra \
  tests/unit/test_subscription_campaigns_release.py \
  tests/unit/test_start_api.py \
  tests/unit/test_appointment_operations_release.py \
  tests/unit/test_subscription_diagnostics_release.py \
  tests/unit/test_subscription_sent_messages_release.py \
  tests/unit/test_subscription_templates_release.py \
  tests/unit/test_subscription_chatbot_release.py \
  tests/unit/test_subscription_devices_release.py \
  tests/unit/test_appointment_consent_release.py \
  tests/unit/test_manual_start_times_release.py \
  tests/unit/test_0056_predeploy_guard.py \
  tests/unit/test_cakto_post_0053_predeploy.py
```

O arquivo `test_subscription_campaigns_release_postgres.py` executou seus
**51 casos com sucesso** na árvore intermediária 427b0cff.
Eles verificam o catálogo sob as identidades reais e negam SELECT do runtime
nas sete tabelas, mesmo com tenant configurado. As provas de alteração de
grants, RLS, checks, FKs, chaves e índices usam transações no banco descartável
de loopback, sempre revertidas. A fixture rejeita hosts externos. A execução foi
no PostgreSQL descartável do CI; não há servidor PG/Docker disponível neste
ambiente local. A árvore final integrada precisa repetir o gate completo.

## Integração no CI

O workflow `subscription-chatbot.yml` mantém os seis jobs existentes: Python,
ponte, DOM, PostgreSQL, Chromium e WebKit. Acrescenta o código, fixtures e
testes de Campanhas aos triggers e comandos, preserva todos os casos anteriores
e conserva o Chrome oficial necessário aos codecs já publicados.

Os testes unitários usam o padrão `test_subscription_campaign*.py`, incluindo
o wiring singular e as suites plurais. Os testes PG usam
`test_subscription_campaigns*_postgres.py`. O gate de ponte inclui os arquivos
`src/campaign-*.mjs` na checagem de sintaxe e `test/campaign-*.test.mjs` nos
testes. O DOM usa `subscription-campaigns.test.mjs`; os dois navegadores usam
`browser_subscription_campaigns.py`, com capturas em
`test-artifacts/subscription-campaigns/`. O gate de PG continua proibindo rede
externa e exige PostgreSQL/Redis descartáveis.

Para qualificar a revisão final ainda são necessários os resultados executados
de PG, ponte, DOM e navegadores, o gate Python completo e a comparação dos
workflows gerais com a produção efetiva a2f84ea. O baseline observado contém falhas
históricas explícitas; esta nota não declara o conjunto geral verde. Backup,
migração, deploy e verificação somente de leitura em produção permanecem para
a fase de publicação autorizada da revisão qualificada.
