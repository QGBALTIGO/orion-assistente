# Enviar Mensagem — qualificação da etapa 11

## Estado

**Publicado e encerrado após conferência ao vivo**, em 05/11/2026, pela
[PR 263](https://github.com/QGBALTIGO/orion-assistente/pull/263).
O merge de produção é `32a90c0fd003c5fbac70c1bd541bf9ce20171040`; sua árvore
`14d352c68feb56da64f0f6995c579dc89a172148` é a mesma da candidata qualificada
`3c4014556aa82ba322ebd94debc6e56a6ee300e7`.
A PR começou com o registro da etapa 09 e do bloqueio da etapa 10.
O usuário autorizou manter funções bloqueadas por plano como pendentes e
prosseguir pelas acessíveis, sem compras ou nova confirmação por esse motivo.

Base anterior de produção: `d17cb67a29fca8293299ac3b2b969601b540075a`, árvore
`505fd9e7b588dac70bd5c325b5c8d9e063aa47d1`, após Mensagem em Massa.
O contrato e os limites de observação estão nos registros
[de observação](direct-messages-observation.md) e [de implementação](direct-messages-contract.md).

## Verificações durante a implementação

- Permissões e rotas: o teste de empresa inativa identificou a necessidade
  de fechar também a nova funcionalidade. A política mantém somente Conta
  e configuração disponíveis nessa condição, preservando a matriz dos outros
  nichos. Os 77 casos focados de feature/workspace passaram; o conjunto final
  de wiring e classificação de rotas passou em 59 casos, e o shell DOM em 15.
- Backend: 418 casos focados passaram, incluindo 97 novos de política, API,
  snapshot, recuperação, cancelamento e autoridade do envio direto, mais as
  regressões de Massa, Templates e Mensagens Enviadas selecionadas.
- Publicação: 568 casos focados passaram para backup, cadeia de migração,
  barreiras de catálogo e inicialização, incluindo os ancestrais afetados.
- Texto: a revisão de integração encontrou remoção de espaços por um helper
  compartilhado. Enviar Mensagem passou a preservar seu texto literal;
  Templates conserva sua normalização existente.
- Interface: os rascunhos foram separados por aba para preservar os campos
  ao alternar Texto, Mídia e Template, sem misturar modalidades no payload.
  Os 73 casos DOM focados passaram, incluindo os oito tipos de template,
  os 15 formatos aceitos no upload, o retorno do nome canônico do arquivo,
  a recuperação de pedidos e a rejeição comprovada do cancelamento obsoleto.
- Chrome local: 15 cenários passaram na primeira execução. O cenário restante
  aguardava um endpoint diferente do utilizado por Minha Conta; a expectativa
  foi corrigida para `/account/business-identity` e esse caso passou isoladamente.
  Não se apresenta esse resultado como uma única execução integrada de 16 casos.
  O binário oficial do WebKit foi posteriormente instalado, mas o ambiente
  local não tinha suas bibliotecas nativas. A aprovação integrada do WebKit
  foi obtida no CI, conforme a candidata final abaixo.

Essas contagens são execuções locais sobrepostas, realizadas durante a
implementação, e **não são somadas** nem substituem a qualificação da árvore
final. Nenhuma delas é um teste de envio real do WhatsApp.

## Primeira candidata e correção de foco

A primeira candidata, `8c87f9b8fdc73d6f8bef90d4d1154ce198ee0e04`, árvore
`127cc78f70415b8f1aba145e8679ed3f5fabf215`, executou o
[workflow 42057771560](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42057771560).
API (2.759), DOM (558), ponte (488) e Chrome (121) passaram.
O WebKit interrompeu após 100 aprovações e uma falha no retorno de foco ao
botão Revisar mensagem depois de fechar a revisão de um template; 20 cenários
do engine ainda não haviam executado. Os casos não selecionados do outro
engine não contam como cobertura concluída. O ajuste fica restrito à
restauração de foco e mantém a expectativa de acessibilidade do teste.
PostgreSQL concluiu **588 aprovações em 740,46 segundos**, incluindo os 29
casos novos de catálogo/publicação e os 22 funcionais. O log comprova o
upgrade 0070 → 0071. A comparação geral é registrada separadamente, com
limites para os jobs incompletos. Essa candidata **não foi publicada**.

A correção mantém o foco imediato e o reafirma no próximo frame somente
se o fechamento nativo o liberou e o contexto continua válido, sem outra
modal ou campo escolhido pelo usuário. Dois casos DOM reproduzem a perda
tardia e protegem a mudança de foco/contexto. Os 75 casos DOM do módulo
passaram, assim como o cenário Chrome dos oito templates reexecutado
isoladamente. A expectativa WebKit original permanece intacta; a correção
passou na execução integrada da segunda candidata, antes da publicação.

Os artefatos dos dois engines da primeira candidata foram baixados e
conferidos por SHA256. Doze capturas, cobrindo uma combinação de tamanho e
tema por engine entre formulário, revisão e resultado, não apresentaram
outra inconsistência visual material. Isso não substitui o gate WebKit
da correção de foco, aprovado posteriormente na segunda candidata.

## Candidata final: seis gates aprovados

O [workflow dedicado 42060576006](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006),
tentativa 1, executou a candidata `3c4014556aa82ba322ebd94debc6e56a6ee300e7`
sobre a base `d17cb67a29fca8293299ac3b2b969601b540075a`. Os logs registram o
checkout do merge de teste `35e03567b35cf40c937d07de149359cf9658e8d8`.
Os seis jobs terminaram em `success` na mesma revisão:

| Grupo | Job | Aprovados |
|---|---|---:|
| API, políticas, execução, arquivos reais e publicação | [127360778877](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006/job/127360778877) | 2.759 |
| PostgreSQL real: migração, RLS, papéis, integridade, concorrência e quotas | [127360779098](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006/job/127360779098) | 588 |
| DOM, formulários, foco, estados e navegação | [127360778034](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006/job/127360778034) | 560 |
| Ponte, efeitos e isolamento do legado | [127360778969](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006/job/127360778969) | 488 |
| Chrome, celular/tablet/desktop e dois temas | [127360778767](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006/job/127360778767) | 121 |
| WebKit, nos mesmos fluxos | [127360778960](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006/job/127360778960) | 121 |
| **Total da candidata final** | **6 jobs aprovados** | **4.637** |

Não houve falha nem teste ignorado nesses seis grupos. Cada job de navegador
desselecionou os 121 casos do outro engine e executou todos os seus 121 casos;
essa seleção não representa testes ignorados. A API apresentou seis warnings.
Os resultados da primeira candidata e os testes locais não entram nesse total.

O PostgreSQL concluiu em 561,61 segundos, incluindo os 29 casos novos de
catálogo/publicação e os 22 funcionais. O log registra o upgrade
`0070_subscription_bulk` → `0071_subscription_direct`. O WebKit concluiu em
524,82 segundos e aprovou a asserção original de retorno de foco; ela não foi
removida nem flexibilizada. O Chrome concluiu em 363,69 segundos.

O PostgreSQL inclui confirmação e gravação opcional de template na mesma
transação, rollback, repetição da mesma chave, colisão de chave entre Massa
e Enviar Mensagem, dez/onze contatos, mídia e áudio, origem do histórico,
cancelamento e revogação antes de ler conteúdo privado.

## CI geral: comparação e limites do corte

A inventariação remota da candidata final conferiu a paginação: 26 workflows
incluindo o dedicado; os 25 workflows gerais continham 62 jobs. O corte
preservado, coletado em 05/11/2026 entre 16:14:57Z e 16:15:01Z, continha
**46 aprovações, 13 falhas herdadas comparadas e 3 jobs ainda em execução**.
Não foram atribuídos a esse corte resultados que chegaram depois dele.

Os 13 jobs encerrados com falha foram comparados por identidade dos testes,
multiplicidade e causa com a primeira candidata, que havia sido comparada
independentemente com a PR 262. Não houve nova identidade de falha nesse
conjunto concluído. As contrapartes são:

| Escopo geral | Job candidata final | Job primeira candidata | Resultado ou limite herdado |
|---|---|---|---|
| Planos, contratos | [127360780936](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576038/job/127360780936) | `127350751934` | 24 falhas Python; mesmos IDs, multiplicidades e causas |
| Publicação do link direto | [127360779370](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576559/job/127360779370) | `127350751132` | Mesma asserção de revisão; 37 testes anteriores aprovados |
| Conexão WhatsApp, PostgreSQL | [127360780038](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576194/job/127360780038) | `127350751287` | 1 falha e 288 aprovações; mesma causa |
| Campos, contratos backend | [127360776683](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060575783/job/127360776683) | `127350752094` | 2 falhas e 303 aprovações; mesmas causas |
| Campos, DOM | [127360776213](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060575783/job/127360776213) | `127350752408` | 6 falhas e 114 aprovações; mesmos diagnósticos TAP |
| Histórico de clientes, PostgreSQL | [127360778100](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576237/job/127360778100) | `127350752184` | 3 falhas e 313 aprovações; mesmas causas |
| Ponte e painel, suíte global | [127360778778](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576171/job/127360778778) | `127350751486` | 19 falhas e 821 aprovações; nenhuma falha nova |
| Portal profissional, PostgreSQL | [127360779772](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576599/job/127360779772) | `127350751029` | Mesma asserção de revisão na preparação |
| Comunicações por atendimento, PostgreSQL | [127360780504](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060575909/job/127360780504) | `127350751997` | 1 falha e 295 aprovações; mesma causa |
| Planos, PostgreSQL | [127360781658](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576038/job/127360781658) | `127350751448` | 1 falha e 191 aprovações; mesma causa |
| Planos, Chrome e smoke legado | [127360781455](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576038/job/127360781455) | `127350751796` | 72 cenários aprovados; mesma falha posterior no smoke legado |
| Campos, Chrome | [127360776939](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060575783/job/127360776939) | `127350752303` | 29 falhas e 169 aprovações; mesmas causas |
| Campos, WebKit | [127360776724](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060575783/job/127360776724) | `127350752529` | 52 falhas e 141 aprovações; mesmas causas |

Na comparação TAP da ponte global, a única diferença de diagnóstico era um
UUID sintético de `operation_id`. Depois de normalizar somente esse valor,
asserções, locais de chamada e demais diagnósticos coincidiram. As 19 falhas
legadas continuam sendo falhas; o resultado não equivale à aprovação da suíte
global da ponte.

O [workflow de hardening 42060575647](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060575647)
terminou com seus dois jobs aprovados: 1.043 contratos e 761/761 testes de
transporte. O relatório `npm audit` apresentou zero vulnerabilidades. Seu
artefato global continua registrando 840 testes, 821 aprovações e 19 falhas
legadas, sem novas falhas no comparativo. Esses números se sobrepõem a outros
workflows e não são adicionados às 4.637 execuções dedicadas.

### Adendo terminal posterior ao corte, às 16:26:43Z

- Conexão WhatsApp/WebKit, job [127360779930](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576194/job/127360779930),
  concluiu em `success` às 16:15:02Z. No corte anterior, seus testes já haviam
  passado e o upload de artefatos ainda estava em andamento.
- FATIA/pytest, job [127360778589](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576171/job/127360778589),
  terminou por timeout de 20 minutos às 16:22:34Z, com progresso de 13% e sem
  resumo final. Seu conjunto completo de falhas não foi comparado nem aprovado;
  os passos posteriores de Docker e health ficaram sem execução.
- FATIA/ASR, job [127360777774](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576171/job/127360777774),
  terminou `cancelled` às 16:25:33Z durante o smoke de áudio sintético. Os
  quatro testes unitários/API e a construção da imagem Docker passaram,
  mas o smoke foi cancelado sem saída de inferência e sem causa interna
  identificada. A duração de 25 minutos e 17 segundos é compatível com o
  limite de 25 minutos do job; não comprova erro de rede, instalação ou provedor.

Nesse adendo, os 62 jobs gerais correspondem a **47 aprovações, 13 falhas
herdadas, 1 timeout e 1 cancelamento**. A classificação agregada do GitHub é
47 `success`, 14 `failure` e 1 `cancelled`, incluindo o timeout entre as falhas.
O repositório inteiro não está aprovado; a
comparação completa dos jobs incompletos permanece fora da evidência desta
entrega. Os seis gates dedicados, por sua vez, foram integralmente aprovados
na árvore publicada.

## Publicação e confirmação do schema

A migração `0071_subscription_direct` acrescenta a origem em lotes e pedidos,
com restrições e FK composta; não cria outro transporte. A árvore do merge
publicado foi conferida localmente e corresponde exatamente à candidata final.

| Serviço | Deployment ativo | Revisão | Estado confirmado |
|---|---|---|---|
| API | `e003d18b-61ed-4f84-821e-ad78b14da855` | `32a90c0fd003c5fbac70c1bd541bf9ce20171040` | `SUCCESS`, online, 1/1 réplica, zero crashes |
| Worker | `7c1fba79-be91-4e51-9891-e27150418720` | `32a90c0fd003c5fbac70c1bd541bf9ce20171040` | `SUCCESS`, online, 1/1 réplica, zero crashes |
| Ponte | `91a9b644-b9f3-4d97-80f6-5db624275c47` | `d17cb67a29fca8293299ac3b2b969601b540075a` | `SUCCESS`, online, 1/1 réplica, zero crashes |

A nova tentativa automática da ponte,
`cc659c3e-bcce-4d32-be3a-e53d7318b247`, foi marcada `SKIPPED` pelos watchpaths.
Não houve rebuild forçado. O diretório `qr-bridge` tem a mesma árvore
`bd56e21e110562202e2e99ac8e2dc04594629637` nas revisões antiga e nova.
Enviar Mensagem reutiliza `bulk_action` e `X-Orion-Bulk-Protocol: 1`, já
suportados pela ponte ativa.

O predeploy registrou a seguinte ordem em 05/11/2026, UTC:

| Horário | Evidência |
|---|---|
| 16:17:12.333103641Z | `private_backup_archive_verified` |
| 16:17:12.502592709Z | `orion_subscription_direct_migration_start source=0070_subscription_bulk target=0071_subscription_direct` |
| 16:17:13.513802639Z | Barreira do worker: `ok=true`, `schema_marker=0071_subscription_direct` |
| 16:17:13.572075693Z | `orion_subscription_direct_schema_ready` |
| 16:17:21.541085747Z | Barreira do runtime da API: `ok=true`, `schema_marker=0071_subscription_direct` |
| 16:17:22, registro recebido às 16:17:23Z | Worker inicia 25 funções |
| 16:17:24Z | Inicialização dos processos da API concluída |

O backup privado foi verificado antes do marcador de início da DDL. Seu objeto
é `production/subscription-direct-0071/pre-schema-0070_subscription_bulk-20261105T161711Z-cbc77d9e69dd4e20aea1ec97d0743049.dump`.
API e worker confirmaram o catálogo 0071, incluindo os contratos ancestrais,
antes da execução.

Redis e PostgreSQL também estavam online, com 1/1 réplica. A verificação final
encontrou zero avisos ou notificações críticas ativas, nenhum deployment
pendente e nenhuma alteração staged. Não houve falha nova, retry ou mudança
de configuração nesta publicação. A janela histórica ainda mostrava o build
do worker que falhou na etapa 09 e foi resolvido naquela entrega; ele não é
uma falha da etapa 11.

## Conferência ao vivo e encerramento

O Chrome conectado abriu `/painel?aba=enviar-mensagem` na produção publicada.
Foram conferidos o novo item do menu, as abas Texto/Mídia/Template e seus
campos e limites no padrão visual do Orion. A opção Salvar como template
exibiu e ocultou o campo Nome conforme marcada ou desmarcada. A aba Template
mostrou corretamente a biblioteca vazia.

Ao terminar, a tela voltou para Texto com mensagem e número vazios e Salvar
como template desmarcado. A leitura de erros retornou `errors=[]`. Não houve
upload, revisão, confirmação, criação de template, alteração de contato nem
envio real durante essa conferência.

**Etapa 11 encerrada.** A execução funcional foi qualificada nos ambientes
descartáveis e sintéticos; a publicação, o backup e as barreiras foram
verificados na infraestrutura real; a interface publicada foi conferida no
Chrome conectado. Os limites de observação da referência e do CI geral
permanecem documentados e não são convertidos em afirmação de equivalência
integral para comportamentos que não foram observados ou testados.
