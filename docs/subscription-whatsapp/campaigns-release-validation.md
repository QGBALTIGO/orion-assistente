# Etapa 07 — publicação e validação de catálogo

Estado em 2026-11-05: **publicado pela PR258, com backup, migração, startup e
deployments verificados; interface conferida ao vivo no Chrome**. A árvore
publicada passou os seis gates dedicados, com 3.110 execuções aprovadas.
O histórico intermediário abaixo é preservado; os resultados finais e os
identificadores da publicação estão registrados ao final.

A conferência da interface em produção não salvou grupos ou campanhas, não
agendou e não enviou mensagens reais. A base posterior PR259 está publicada,
saudável na conferência de infraestrutura e com a repetição específica do seu
gate WebKit aprovada às 07:02:04 (`America/Cuiaba`). **A etapa 07 está encerrada;
a etapa 08 passa à observação**, preservando os limites de evidência abaixo.

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
também sem as chaves de transporte. A versão final inclui as cinco variáveis
SSH/HTTPS nessa exclusão. A supervisão da API e do túnel só começa depois
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
no PostgreSQL descartável do CI; não havia servidor PG/Docker disponível no
ambiente local. A árvore final integrada repetiu o gate completo, com os 338
casos aprovados, incluindo esses 51 de catálogo.

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

A revisão final executou os seis gates e a comparação dos workflows gerais
com a produção efetiva `a2f84ea`. O baseline contém falhas históricas explícitas;
esta nota não declara o conjunto geral verde. Backup, migração, deploy e
conferência somente de leitura em produção foram observados após a qualificação,
com as evidências abaixo.

## Qualificação e identidade da árvore publicada

O head `e5afccda2880d37121ab9467c36bfefe7095c5a4` e o checkout sintético
`e2269ecac14e7970706ba586be1acfd1f612cba5` têm a árvore
`503dcbec9eb05252460ed545acb64f1901061cd1`. O
[run 41934922779](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41934922779)
aprovou 1.868 testes Python, 338 PostgreSQL, 411 da ponte, 345 DOM, 74 no Chrome
oficial e 74 no WebKit: **3.110 execuções aprovadas**. O PostgreSQL inclui 33
casos funcionais de Campanhas, 51 de release e 254 anteriores.

A comparação geral concluiu 64 de 64 jobs lógicos, sem regressão nova
identificada. Isso não elimina o passivo histórico: o candidato teve 169 falhas
e 43 erros na suíte ampla, além de 19 falhas Node, coincidentes com o baseline
em identificador, fase e multiplicidade. As demais causas históricas e os
limites de comparação estão no
[registro de validação](campaigns-validation.md#qualificação-final-da-pr258)
e na [PR258](https://github.com/QGBALTIGO/orion-assistente/pull/258).

O merge ocorreu em **2026-11-04 às 20:28:29 (`America/Cuiaba`)**, no commit
`23b78bf42086766694a992474f078c2a4ed0a2eb`. O commit real tem os pais
`a2f84ea6781a08ad8a97baa5ffce951ae60f3bdc` e
`e5afccda2880d37121ab9467c36bfefe7095c5a4`, com a mesma árvore qualificada.
A ref de produção foi conferida após o merge.

## Publicação original: backup, migração e serviços

Os horários desta seção são de **2026-11-04, em `America/Cuiaba` (UTC−4)**.
Os três deployments concluíram com `SUCCESS` no commit `23b78bf`:

| Serviço | Deployment | SUCCESS observado |
| --- | --- | --- |
| API | `88764537-918c-4f09-82c2-ad00c0dc5387` | 20:30:44 |
| Worker | `b6ed0694-9ac3-467d-aa53-d6fd44619f95` | 20:32:29 |
| Ponte WhatsApp | `51075974-ea74-4b3a-abf7-d33b93357ad6` | 20:29:13 |

Os logs específicos de startup mostram a ordem necessária:

1. Às **20:30:27**, `private_backup_archive_verified` foi registrado antes do
   início da migração. O prefixo privado corresponde à release 0068 sobre 0067.
2. O DDL avançou de `0067_subscription_diagnostics` para
   `0068_subscription_campaigns`; às **20:30:29**, os marcadores
   `orion_subscription_campaigns_schema_ready` e
   `subscription_campaigns_schema_ready` confirmaram o catálogo.
3. Às **20:30:37**, o probe da API registrou `ok=true`,
   `status=subscription_campaigns_schema_ready` e
   `schema_marker=0068_subscription_campaigns`, antes do Uvicorn. Os três
   processos concluíram startup às **20:30:40**.
4. Às **20:32:22**, o worker aprovou as barreiras 0066, 0067 e 0068, com
   `ok=true` e `schema_marker=0068_subscription_campaigns`. O ARQ iniciou às
   **20:32:29**. O marcador opcional
   `orion_plan_permissions_0050_worker_ready` não apareceu no filtro limitado
   e não é apresentado como evidência observada.

A ponte registrou conexão e início do serviço privado às **20:29:11**. Cinco
avisos sanitizados de adoção da sessão principal com HTTP409 ocorreram entre
20:29:11 e 20:29:30. Não reapareceram na consulta limitada de 20:32:30 a
20:41:26. A causa não foi estabelecida; não se afirma que eram anteriores à
release. Nenhum envio ou teste real de sessão foi feito para investigá-los.

Na conferência limitada às 20:41:26, API, worker e ponte estavam Online no mesmo
commit; Redis e PostgreSQL também estavam Online, com uma réplica ativa cada,
nenhum crash, trabalho pendente ou alerta ativo observado. Essa evidência de
infraestrutura não substitui um teste de entrega de mensagem.

## Conferência ao vivo e versão posterior

Em **2026-11-05, das 06:50 às 06:53 (`America/Cuiaba`)**, o Chrome conectado
do usuário permitiu conferir o menu, a lista vazia de Campanhas, o formulário,
a alternância texto/template, os campos de dispositivo/agendamento/fuso/dias
e a lista/formulário de grupos no painel publicado. O console capturado não
apresentou avisos ou erros. Os formulários foram abandonados sem salvar e a
tela voltou à lista original; não houve criação de dados, agendamento ou envio
real. As capturas da lista e do formulário foram registradas fora do repositório.
Na continuidade da conferência, a captura completa do formulário foi
inspecionada visualmente, sem cortes ou elementos sobrepostos identificados;
a tela retornou à lista sem salvar.

A produção já contém a [PR259](https://github.com/QGBALTIGO/orion-assistente/pull/259),
commit `19fbe9a98cc59dbc927f69517666f77ec2eb8d6b`, árvore
`6ffd7381c77fcadfa2d64af65d91d5ded4b0eb6a`, cujo primeiro pai é `23b78bf`.
Os arquivos próprios de Campanhas permaneceram iguais. Às **06:55:51 de
2026-11-05 (`America/Cuiaba`)**, foram conferidos os deployments atuais:

| Serviço | Deployment da PR259 | Estado observado |
| --- | --- | --- |
| API | `26a539e7-6ac0-4a30-8747-3fdf4e8e4a78` | SUCCESS / Online, 1 réplica ativa |
| Worker | `d08e669e-b71c-423f-8851-4f47279a9027` | SUCCESS / Online, 1 réplica ativa |
| Ponte WhatsApp | `9c3aa0bd-43ab-4217-afd8-1a6db4226a6c` | SUCCESS / Online, 1 réplica ativa |

Os três correspondem a `19fbe9a`. Redis e PostgreSQL também estavam Online,
sem réplicas em crash. Não havia trabalho pendente nem alertas de aviso/críticos
na leitura. A conferência da nova base foi somente leitura; o backup e a migração
0068 acima continuam associados à publicação original da PR258.

Na validação da base posterior, houve erro de página capturado por `pageerror` em
`/api/v1/account/business-identity` durante troca de perfil no cenário
`test_filters_expiry_revocation_empty_and_other_niche_do_not_admit[webkit]`.
A tentativa 2 foi cancelada. A falha e esse cancelamento permanecem como
histórico; a causa da falha inicial não foi comprovada.

A tentativa 3 do
[run 41950107314](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41950107314),
job `127201760767`, terminou em **SUCCESS em 2026-11-05 às 07:02:04
(`America/Cuiaba`)**, com **74 aprovados e 74 deselecionados em 392,13 segundos**.
O checkout `0a59ddc7a67a0283e48aed6219ddfd00479b9616` tem a árvore
`6ffd7381c77fcadfa2d64af65d91d5ded4b0eb6a`, idêntica à produção `19fbe9a`.
Os deselecionados não são contados como testes executados. Somente o WebKit foi
repetido; os resultados dos outros cinco jobs foram reaproveitados e não são
apresentados como novas execuções dessa tentativa.

Com essa evidência final, a publicação e a conferência ao vivo da etapa 07
estão encerradas. Mensagens Programadas passa à observação após essa
confirmação. Os registros da falha inicial e do cancelamento são preservados,
sem atribuir uma causa não demonstrada nem ampliar as alegações para envio real.

## Limite da equivalência com o BotBot

A criação/edição completa de Campanhas na referência exige um complemento
pago. O usuário recusou a compra; não comprar, ativar nem solicitar novamente
aprovação desse complemento. Os fluxos inacessíveis continuam explicitamente
não observados. Os testes sintéticos e a conferência da interface publicada
validam o contrato do Orion, sem demonstrar paridade integral com a execução
privada bloqueada da referência. Não houve compra nem tentativa de contornar
essa restrição.
