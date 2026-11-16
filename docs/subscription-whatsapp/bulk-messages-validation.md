# Etapa 09 — validação de Mensagem em Massa

## Estado

**Etapa 09 publicada e encerrada em 2026-11-05**, após os seis gates
dedicados, publicação dos três serviços e conferência no Chrome conectado.
A [PR 262](https://github.com/QGBALTIGO/orion-assistente/pull/262) foi integrada
no commit `d17cb67a29fca8293299ac3b2b969601b540075a`, preservando a árvore
qualificada `505fd9e7b588dac70bd5c325b5c8d9e063aa47d1`.

A base anterior à publicação era
`149c43fba44b3f93bdcd90920751d79dc231d32a`, árvore
`36391408683510b62029c94077c85002a98c349e`, após o encerramento de
Mensagens Programadas e seu ajuste de texto nas PRs 260 e 261.

O escopo e as decisões próprias do Orion estão no
[contrato](bulk-messages-contract.md). Os comportamentos efetivamente vistos
no Chrome conectado e seus limites estão na
[observação](bulk-messages-observation.md).

## Evidência local

| Grupo | Resultado | O que verifica |
| --- | --- | --- |
| API, schemas, políticas e persistência do módulo | 116 passaram | Limites, texto literal, oito templates, linhas repetidas, identidade da revisão, confirmação, recuperação, cancelamento e projeções. |
| Integração do módulo com permissões, rotas, broker e histórico | 42 passaram | Restrição ao nicho, ordem justa de admissão, limpeza anterior aos gates, delegação de autoridade e ausência de reenvio genérico. |
| Publicação, reinicialização e runtimes compartilhados | 536 passaram | Backup antes de DDL, ancestralidade, cadeia de barreiras, reinício na revisão 0070, opt-out e preservação de Campanhas e Programadas. |
| Ponte de Massa, Programadas, Campanhas e falhas compartilhadas | 108 passaram | Transporte sintético, recibos, autorização por parte, incerteza, capacidades compartilhadas e assets privados. |

Os grupos acima têm interseções com outras execuções focadas e não são
somados para produzir um total único. A compilação Python, as verificações
de sintaxe JavaScript e a verificação de whitespace foram executadas nos
arquivos concluídos. Os resultados integrados da árvore final estão abaixo.

Os testes detectaram antes da publicação dois pontos de integração corrigidos:
o reconhecimento de `bulk_action` no despachante da ponte, e as duas listas
de revisões aceitas no predeploy, necessárias para reiniciar após 0070.
Os testes antigos de cadeia passaram a incluir a barreira adicional; o teste
de opt-out agora exige a invalidação de Massa além dos dois módulos anteriores.

## Critérios de qualificação

- Contratos Python completos e DOM do painel e de seus consumidores.
- PostgreSQL real descartável: migração, RLS, grants, concorrência,
  idempotência, linhas repetidas, cadência, cancelamento, origem e recibos.
- Chrome e WebKit, em celular, tablet e desktop, nos dois temas.
- Comparação das falhas gerais do repositório com a base, distinguindo
  problemas anteriores de regressões da mudança.
- Igualdade entre a árvore qualificada e a árvore a integrar.

A validação completa de PostgreSQL 16, Chrome oficial e WebKit é executada
pela infraestrutura de CI já existente, com banco e
destinatários sintéticos e saídas externas bloqueadas. Não se usa banco de
produção como banco de teste.

### Retomada e correções verificadas

A implementação recuperada foi transferida para uma cópia isolada depois de
comprovar que sua árvore-base era idêntica à produção. A primeira candidata
da retomada é `131dd83531324df1b5eb9c431d013eb2c8e70192`, árvore
`6d70247edc1484f6f97f73c8c3f98602d3d570e9`.

O navegador local de teste encontrou um fechamento ausente no resumo da
revisão. No celular, isso colocava o rodapé de confirmação dentro da área
rolável e fora do viewport. O fechamento foi corrigido; uma regressão DOM
verifica a hierarquia e o fluxo real verifica a geometria. Os 14 cenários
locais Chromium passaram em 390×844, 768×1024 e 1440×900, nos dois temas.
A suíte DOM específica passou 61 casos. Esses resultados não substituem
os gates de Chrome oficial e WebKit.

Os testes funcionais de PostgreSQL agora exercitam os endpoints reais,
autenticação, CSRF, papéis, concorrência, origens, recibos e recuperação.
A coleta local e os testes unitários não são apresentados como prova de
execução no banco. O PostgreSQL local instalado não pode iniciar sob o
mapeamento de usuários disponível; o banco descartável do CI é a referência.

A conferência da publicação identificou uma janela entre as versões da API
e da ponte. O protocolo explícito do poll impede que uma ponte anterior
admita trabalho de Massa ou receba seus descritores. A limpeza e a revogação
de trabalho antigo permanecem ativas. O encerramento exigiu confirmar os
deployments e a interface, sem depender da ordem dos builds.

O CI geral mantém falhas anteriores. O comparativo de transporte da primeira
candidata registrou `new_failures: {}` e `global_suite_passed: false`:
820 testes passaram e os mesmos 19 IDs legados falharam. Campos também
preservou os dois IDs de contratos e seis IDs de DOM já presentes na base.
Os resultados completos da candidata final foram vinculados na PR antes da
integração e estão discriminados abaixo; contagens sobrepostas não são somadas.

No PostgreSQL da primeira candidata, os 36 cenários funcionais novos e o
contrato de migração passaram. A execução parou depois de 158 aprovações em
um teste anterior de concorrência entre Campanhas e Programadas: a exclusão
mútua passou, mas a asserção final consultava apenas efeitos de Programadas.
A nova ordem justa pode admitir Campanhas primeiro. A regressão passa a
conferir o efeito e o destinatário em execução no conjunto dos módulos,
mantendo a exigência de exatamente um trabalho admitido. A nova candidata
também exercita a compatibilidade entre versões da ponte no banco real.

Na segunda candidata, Chrome concluiu 105 cenários, DOM 485 e ponte 488.
WebKit parou após 76 aprovações em uma navegação de Programadas, antes dos
cenários de Massa. O teste verificava a ausência de um item de menu antes
de concluir o carregamento da conta e podia iniciar o próximo documento com
uma consulta de identidade em andamento. Uma reprodução local com resposta
sintética atrasada confirmou duas consultas abandonadas. A sincronização
passa a aguardar a resposta e o estado carregado antes de navegar novamente;
não se filtram erros nem se removem as asserções de acesso ou ausência de
mutação. Essa correção pertence ao teste, sem alteração no código do produto.

A mesma rodada esgotou o tempo do gate da API durante o download de pacotes
do espelho Azure do Ubuntu, antes de executar testes. Os dois passos de
instalação do decoder passam a usar o repositório oficial do Ubuntu via HTTPS,
preservando versões disponíveis, componentes, chaves e verificação de
assinaturas do APT. A preparação tem prazo limitado; o decoder real e todos
os gates foram mantidos na qualificação final.

## Qualificação final da PR 262

O [workflow dedicado 42043961467](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467)
passou nos seis gates sobre a candidata
`8d71a4d635d1652ef2805b67cdc1a8a6a1e97655`. O checkout de integração
`b3e98f6c98fd3e094b6c7fddadaf200fef72d665` e o merge publicado
`d17cb67a29fca8293299ac3b2b969601b540075a` preservaram a árvore testada
`505fd9e7b588dac70bd5c325b5c8d9e063aa47d1`.

| Gate | Job | Resultado |
| --- | --- | ---: |
| API, motor, arquivos reais e publicação | [127301984773](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467/job/127301984773) | 2.559 aprovados |
| PostgreSQL real, migração, RLS e concorrência | [127301984731](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467/job/127301984731) | 537 aprovados |
| DOM, formulários, estados e navegação | [127301984337](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467/job/127301984337) | 485 aprovados |
| Ponte, efeitos e isolamento do legado | [127301984811](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467/job/127301984811) | 488 aprovados |
| Chrome oficial | [127301984851](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467/job/127301984851) | 105 aprovados |
| WebKit | [127301984800](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467/job/127301984800) | 105 aprovados |
| Total do workflow dedicado | | **4.279 execuções aprovadas** |

As contagens representam execuções dos seis conjuntos, com cobertura
sobreposta, e não uma quantidade de casos funcionais únicos. Execuções
locais, outras suítes e comparações de regressão não entram nesse total.
O PostgreSQL real concluiu 42 cenários funcionais de Massa, 56 de schema e
publicação de Massa e 439 anteriores, sem falhas ou casos ignorados. Foram exercitados
os endpoints reais com cookies/CSRF, RLS, papéis separados, concorrência,
idempotência, origens, cadência compartilhada, revogação, recibos e
compatibilidade entre versões da ponte, usando somente dados sintéticos.

O decoder real foi instalado e utilizado nos gates finais. A correção da
sincronização de navegação foi aprovada nos dois navegadores sem filtrar
erros, retirar asserções ou adicionar tentativas. Celular, tablet, desktop
e os dois temas foram cobertos pelo workflow dedicado.

## Comparação do CI geral e limites

Na conferência final, os **64 jobs gerais** encerraram com **49 aprovações,
13 falhas com identidades e causas anteriores, um timeout de FATIA e um
cancelamento durante a preparação de transport-hardening**. Na classificação
do GitHub, são 49 `success`, 14 `failure` e um `cancelled`; o timeout está
incluído nos 14 estados de falha. O workflow dedicado acima não entra nessa
contagem. Nenhum job geral ficou pendente, mas a qualificação geral permanece
incompleta e o repositório inteiro não está aprovado.

Os IDs e causas das 13 falhas completas coincidem com a candidata anterior
e a base da PR 260. O comparativo preserva identidade, causa e multiplicidade
nos contratos e DOM de Campos, contratos e PostgreSQL de Planos, smoke legado
de Planos no Chrome, navegadores de Campos, publicação de links, portal
profissional, conexão WhatsApp, comunicações de agendamento, histórico de
clientes e suíte geral da ponte. O [comparativo oficial da ponte](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961550)
registrou `new_failures: {}` e `global_suite_passed: false`: as falhas
herdadas permanecem explícitas.

Há dois limites distintos na última candidata:

- A [FATIA, job 127301792984](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043960937/job/127301792984)
  esgotou os 20 minutos do passo de testes a 13% de progresso, sem resumo
  final. Não há conjunto completo de falhas dessa execução para comparar.
- O [transport-hardening, job 127301795238](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961550/job/127301795238)
  foi cancelado durante a instalação lenta do decoder pelo espelho Azure,
  antes de iniciar testes, sem resumo ou artefatos de teste. Esse resultado
  não equivale a aprovação nem a uma falha de asserção do produto.

Nenhuma nova falha funcional foi estabelecida por essas duas execuções
incompletas, mas elas não comprovam ausência de regressões. Os 14 jobs
falhos completos da candidata anterior `95fe9bf589df5dd66f5b59ba23d5418f1c13b644`
foram comparados diretamente com a PR 260; essa é a última comparação
completa da FATIA. A candidata final altera somente a preparação do CI,
o teste de navegação e a documentação em relação a ela, sem mudanças no
código do produto ou runtime. Isso delimita a evidência anterior, sem
converter os resultados incompletos da revisão final em aprovação.

## Publicação e saúde do ambiente

A publicação foi concluída em 2026-11-05 no ambiente `production` do projeto
[strong-creation na Railway](https://railway.com/project/55750961-99ca-495e-b55e-4c07d78e53df?environmentId=162d0407-3d4f-4163-9cb2-0c90154c707f).
Os três serviços confirmaram o mesmo commit
`d17cb67a29fca8293299ac3b2b969601b540075a`:

| Serviço | Deployment | Estado final | SUCCESS em UTC |
| --- | --- | --- | --- |
| API | `e7b5ec2c-a529-4fba-9fb1-ac952d1a2c60` | Online, 1/1 réplica | 14:38:31.479 |
| Worker | `78db156e-c06f-4a56-95b4-5c0d84818924` | Online, 1/1 réplica | 14:44:53.130 |
| Ponte WhatsApp | `91a9b644-b9f3-4d97-80f6-5db624275c47` | Online, 1/1 réplica | 14:37:59.193 |

Na consulta final, os cinco serviços, incluindo Redis e PostgreSQL, estavam
online, com uma réplica ativa e zero réplicas em crash. API, worker e ponte
tinham zero avisos e zero alertas críticos atuais. Não havia trabalho de
publicação pendente nem mudanças preparadas na Railway.

### Backup e barreiras do esquema

| Evento observado nos logs | Horário UTC em 2026-11-05 |
| --- | --- |
| `private_backup_archive_verified` | 14:38:12.567937 |
| Início da migração 0069 → `0070_subscription_bulk` | 14:38:12.618228 |
| `orion_subscription_bulk_schema_ready` | 14:38:13.936490 |
| Barreira de runtime da API: `ok=true`, `status=subscription_bulk_schema_ready`, `schema_marker=0070_subscription_bulk` | 14:38:23.604337 |
| `Application startup complete` nos três processos da API | 14:38:27.797634–14:38:27.797674 |
| Worker: `tested_runtime_versions_match` | 14:44:44.757940 |
| Barreira no deployment do worker: `ok=true`, `status=subscription_bulk_schema_ready`, `schema_marker=0070_subscription_bulk` | 14:44:45.776067 |
| Início das 25 funções registradas no worker | 14:44:53.170921 |

O backup privado verificado antes da DDL foi registrado na chave:

```text
production/subscription-bulk-0070/pre-schema-0069_subscription_schedules-20261105T143811Z-de3e6aef4d894754b43ab7cb2bf597de.dump
```

A verificação corresponde à validação do arquivo, da integridade e da
leitura de retorno exigida pelo procedimento publicado; não é um ensaio
de restauração. As barreiras aparecem como atributos JSON nos logs, mesmo
quando o campo textual da mensagem está vazio. Após iniciar, o novo worker
executou e concluiu normalmente as tarefas periódicas observadas.

### Reconstrução única do worker

O primeiro deployment do worker, `6a4249cd-338e-4c58-869d-dd68973c4e87`,
falhou em `BUILD_IMAGE`: o download da versão fixada do Chisel no GitHub
retornou HTTP 504 em seis tentativas. A falha ocorreu antes das barreiras
de schema e não produziu imagem reutilizável. O worker anterior continuou
online durante a reconstrução.

Antes de repetir, foram conferidos o HEAD `d17cb67a`, a existência de apenas
um ambiente no projeto e a origem já configurada. Foi reaplicada exatamente
uma vez a origem `QGBALTIGO/orion-assistente`, branch
`security/prelaunch-hardening-20261030`, sem pin de commit, sem imagem externa
e sem mudança nas configurações. A nova compilação concluiu as mesmas
verificações de download e hashes, os 12 passos do Dockerfile e o `pip check`.
Ela gerou o deployment final `78db156e-c06f-4a56-95b4-5c0d84818924` na revisão
aprovada. A conferência posterior confirmou a mesma origem e ausência de mudanças
preparadas. O único registro de falha recente no resumo final da Railway
era o build substituído; a instância ativa estava saudável.

A ponte registrou `dispositivo conectado` às 14:37:59.093601 UTC. Houve
retentativas de adoção da sessão principal com HTTP 409 durante a janela
de migração da API; a última observada foi às 14:38:16.960965 UTC. A consulta
posterior, a partir das 14:39, não encontrou novas ocorrências, e a ponte
terminou online, sem avisos atuais ou réplicas em falha.

## Conferência de produção e encerramento

No Chrome conectado, a empresa BaltigoFlix exibiu a navegação Massa, o
histórico e seus três totais, a bancada, o intervalo padrão de 10 segundos
com limites de 10–60, as abas Grupos/Contatos/Entrada manual e a alternância
Texto/Template. A captura no tema escuro confirmou o uso dos componentes
e do padrão visual do Orion. O formulário foi fechado e o histórico voltou
a ser exibido. Não foram capturados avisos nem erros no console.

A conferência foi de leitura e preparação visual: nenhum lote foi criado
ou confirmado, nenhum contato de produção foi alterado e nenhuma mensagem
real de teste foi enviada. A execução completa foi validada nos ambientes
sintéticos descritos acima. Nenhum complemento pago foi contratado. Não se
declara equivalência empírica de funções privadas ou envios da referência
que não foram observados.

**Etapa 09 encerrada.** A observação de Atribuições de Chat, etapa 10,
começou depois da confirmação dos deployments e dessa conferência ao vivo.

Após 0070, o predeploy da versão anterior aceita no máximo 0069. A recuperação
de uma falha de aplicação deve usar uma correção compatível com o novo esquema;
não executar downgrade destrutivo nem supor que republicar a imagem anterior
reverte a migração. O backup privado mantém o procedimento de recuperação
de dados separado do reinício dos serviços.
