# 05 — Mensagens Enviadas: qualificação

## Estado da entrega

**O módulo Mensagens Enviadas foi publicado e conferido ao vivo em 2026-11-04.**
A [PR 252](https://github.com/QGBALTIGO/orion-assistente/pull/252) foi mesclada
em `f58730ac8e380bd2c12286c69c1db3873d3cbf0f`. A árvore incorporada,
`28ac47dc292556b77b80fbc80607077265011a41`, é a mesma do head qualificado
`aaa6fa694633f2f05bf60014286d6b0f14fbb762` e do merge de teste
`435095dc1dc5d67a00f9667da47f3d1fac24d971` usado pelos jobs do CI.

A base anterior é `7164ce5ca9e4c795910ca5255f510f625dad0a11` (Templates,
PR 251), árvore `ef30f3c3a5d0507c2f7dc25619bcb7f2b66d91ec`. Os seis grupos
específicos passaram, a comparação geral não encontrou novas falhas e os três
serviços foram confirmados na revisão publicada. A conferência no Chrome
conectado terminou às 18:18:08Z. Todos os horários deste registro são UTC.

O [registro de observação](sent-messages-observation.md) distingue os
comportamentos vistos no BotBot das decisões independentes de autorização,
idempotência, conteúdo preservado e resultado incerto do Orion. Não houve
reenvio real no BotBot nem uso de destinatários reais para testar o Orion.

## CI final da etapa 05

Fonte: [run 41879361354](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354).
Os logs finais e o encerramento de cada job foram conferidos. O checkout
dos seis grupos corresponde à árvore publicada.

| Grupo | Resultado final | Evidência e escopo |
|---|---|---|
| Python | 1.262 aprovados em 55,95 s | [Job 126747565836](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354/job/126747565836): API, motor, contratos, arquivos reais e publicação. |
| Ponte WhatsApp | 314 aprovados | [Job 126747565639](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354/job/126747565639): protocolo, efeitos, reenvio individual e isolamento do legado. |
| Interface DOM | 251 aprovados | [Job 126747565810](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354/job/126747565810): estados, filtros, paginação, foco, permissões e recuperação após reload. |
| PostgreSQL | 186 aprovados em 213,33 s | [Job 126747565237](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354/job/126747565237): migração, papéis reais, RLS, concorrência, idempotência e quotas. |
| Chrome oficial | 41 aprovados em 204,52 s | [Job 126747565675](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354/job/126747565675): cenários do módulo e regressões anteriores, incluindo codecs reais. |
| WebKit | 41 aprovados em 291,69 s | [Job 126747565477](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361354/job/126747565477): os mesmos contratos de navegação, confirmação e recuperação. |

Não houve falhas ou skips nesses grupos. Cada job de navegador selecionou os
41 casos do seu engine; os 41 casos do outro engine aparecem como deselected,
e foram executados pelo job correspondente. São navegadores reais com larguras
e temas emulados, usando fixtures sintéticas e rede limitada ao servidor de
teste. Os grupos incluem regressões compartilhadas e não devem ser somados
como casos únicos.

## Implementação publicada

- Histórico SQL de tentativas de mensagem, filtros, 30 registros por página,
  desempate determinístico e limite de criação preservado entre páginas.
- Totais por empresa/proprietário, fuso local e métricas de áudio concluído
  separadas de reservas diárias UTC e de durações históricas desconhecidas.
- Nova tentativa individual com snapshot congelado, auditoria e idempotência.
  POST não altera a tentativa original; processamento normal posterior pode
  encerrar uma intenção já expirada que nunca recebeu autorização.
- Reautorização no transporte, ordem da sequência e cooldowns preservados.
  Não recompila regras, executa ASR/HTTP novamente nem repete partes aceitas.
- Novos vínculos de seleção em listas/carrosséis. Carrossel usa os três ativos
  privados preservados; um snapshot incompleto bloqueia reenvio com motivo.
- Motivos da ponte classificados em uma lista explícita e mantidos no ledger
  para reconciliação após reinício. Texto de erro do provedor não é exposto.
  Um código fornecido pelo provedor após relay não pode virar prova de não envio.
- Controller do Orion com descarte de respostas antigas, conteúdo escapado,
  confirmação explícita e recuperação somente por leitura após resposta perdida.
  A identidade da operação pode sobreviver a reload, vinculada à mesma conta e
  empresa, sem persistir mensagem ou contato.

## Evidências locais iniciais

| Grupo | Resultado observado |
|---|---|
| Ponte afetada e consumidores compartilhados | 314 aprovados; zero falhas, cancelamentos ou skips |
| Releases 0060–0065 e barreiras encadeadas | 117 aprovados |
| Permissões, classificação de rotas e workspace | 89 aprovados |
| Backend focado de histórico/dispositivos/broker/áudio | 161 aprovados |
| Gate conjunto Python do workflow específico | 1.262 aprovados, zero falhas/skips em 57,94 s |
| DOM de módulos de assinaturas, shell e sessão | 180 aprovados na regressão inicial |
| DOM próprio de Mensagens Enviadas antes da correção do bootstrap | 24 aprovados após os ajustes de áudio, filtro de contato e retorno de foco |
| Gate conjunto DOM antes da correção do bootstrap | 250 aprovados; zero falhas/skips em 8,60 s |
| DOM próprio após a regressão de bootstrap | 25 aprovados, incluindo conta/empresa divergentes e recuperação por GET |
| Gate conjunto DOM após a correção do bootstrap | 251 aprovados; zero falhas/skips em 9,40 s |
| Coleta local PostgreSQL e navegador novos | 55 casos coletados; a execução real posterior está no CI final acima |

Esses grupos se sobrepõem; os números não devem ser somados como uma única
suíte. A preparação local não substituiu a execução de PostgreSQL/Redis nem
dos navegadores. Esses grupos foram executados posteriormente no CI isolado,
com Chrome oficial para os codecs da aplicação e WebKit, conforme os logs
finais registrados acima.

## Histórico da primeira execução e das correções

O [run 41877434304](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41877434304)
executou o merge de teste `1aec349b1715100a90bcbd068eebc0b7d25f3fc0`, cuja
árvore `134c3b7ac283a4135af025ebf2ddad26ba97ddc0` coincide com a implementação
local. Os logs finais confirmaram 1.262 testes Python, 314 da ponte e 250 de
DOM aprovados, sem falhas nesses três grupos.

Chrome e WebKit chegaram a 21 aprovações e pararam no primeiro cenário novo
de celular: o teste tentava fechar a barra lateral pelo botão do cabeçalho,
coberto pelo drawer aberto. A correção usa o controle visível “Recolher menu”
dentro da barra e mantém a asserção de fechamento. Não altera o produto nem
reduz a cobertura. A nova execução percorreu os 41 cenários de cada engine,
incluindo os que ainda não tinham sido executados nessa primeira rodada.

PostgreSQL concluiu 185 aprovações e uma falha na expectativa de um teste:
`app_runtime` não tem SELECT no ledger privado de efeitos, enquanto a fixture
esperava receber zero linhas. A expectativa foi corrigida para exigir a
negação por privilégio, sem ampliar grants. Os demais contratos de migração,
RLS, concorrência e reenvio passaram nessa execução.

A revisão dos cenários restantes também identificou que o shell inicial,
antes de carregar a empresa, apagava o estado de recuperação da resposta
perdida. O ajuste preserva somente os identificadores validados e mantém a
checagem de conta/empresa antes de qualquer consulta. Um teste DOM reproduziu
a falha antes da correção e passou depois, incluindo contextos divergentes,
remoção de campos extras e ausência de POST. O gate DOM completo passou com
251 testes após o ajuste. O E2E final confirmou a resposta perdida seguida de
reload e recuperação por GET, preservando uma única admissão de reenvio.

## Banco e barreira de publicação

Migração aditiva `0065_subscription_sent_messages` sobre 0064:

- Auditoria privada `subscription_sent_message_resends`, sem UPDATE/DELETE
  pela role support e sem leitura de conteúdo pela role runtime.
- Unicidade da chave e da origem por empresa, FK composta de tenant e RLS
  forçada; uma tentativa não recebe dois reenvios concorrentes.
- Duração concluída nullable e timestamp de conclusão do ASR, sem backfill
  inventado. CHECK exige valor positivo, duração limitada e estado aceito.
- Índices de histórico por empresa/data, dispositivo e telefone.

O predeploy mantém as verificações anteriores, exige backup privado verificado
antes do upgrade e valida o catálogo real após a migração. API/worker aguardam
a barreira 0065 usando apenas catálogos sob a role restrita. A migração não
cria configuração, contato, mensagem nem chamada a provedor.

## Comparação final das suítes gerais

Foram comparados os **23 workflows gerais e seus 56 jobs** com a produção
anterior da PR 251. O workflow específico acima completa o inventário de
**24 workflows e 62 jobs atuais**. A comparação usa `(workflow_id, nome do
job, identidade do teste)` e também `(fase FAILED/ERROR, identidade,
multiplicidade)`. Números de ordem e duração do Node e detalhes variáveis após
o ID pytest foram retirados da identidade, preservando a parametrização.

**Resultado: zero novos IDs de falha e zero diferenças de fase ou
multiplicidade.** Dos 56 jobs gerais, 41 passaram, 12 repetiram falhas de testes
da base e três repetiram falhas fora de testes com a mesma etapa, stack e
causa. Todos foram fechados pelos logs finais, pois os resumos agregados do
GitHub apresentaram atraso durante a execução.

| Grupo geral com falhas de testes anteriores | Resultado final desta revisão | Comparação |
|---|---|---|
| [Python geral](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879362061/job/126747567794) | 7.886 aprovados, 170 falhas, 43 erros e 9 skips; 898,36 s | Mais 103 aprovações que a base de 7.783. As 213 ocorrências repetem exatamente 203 identidades; dez testes aparecem tanto como FAILED quanto como ERROR. |
| [Ponte completa](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879362061/job/126747568019) | 596 aprovados e 49 falhas | Os mesmos 49 IDs da base, que tinha 591 aprovações. |
| [Campos: backend, DOM e navegadores](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361622) | Backend: 303/2; DOM: 114/6; Chromium: 169/29; WebKit: 141/52, em aprovados/falhas | Mesmas identidades nos quatro jobs. |
| [Plans: contratos e PostgreSQL](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361485) | Aprovados/falhas: Python 1.051/25; DOM 185/0; PostgreSQL 191/1, além de dois casos preparatórios aprovados | Os mesmos 25 IDs dos contratos e um ID do PostgreSQL. |
| [Cadastro e histórico: PostgreSQL](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361672/job/126747566228) | 313 aprovados e três falhas | Mesmas três identidades. |
| [Comunicações: Node](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361933/job/126747570305) | 536 aprovados e 30 falhas | Mesmas 30 identidades. |
| [Segurança da ponte](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361436/job/126747565984) | 64 aprovados e 30 falhas | Mesmas 30 identidades. |
| [Conexão única: PostgreSQL](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361739/job/126747567413) | 288 aprovados e uma falha | Mesma identidade. |

As três falhas fora de testes foram comparadas pelos comandos e pelas stacks,
sem usar apenas o exit code:

- [Acesso profissional — PostgreSQL](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361508/job/126747565978):
  o mesmo assert inline, na linha 5, exige a revisão fixa
  `0058_professional_scope`. A base já aplicava 0064 e esta revisão aplica 0065.
- [Link direto — one-click](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361562/job/126747567686):
  os 37 testes passaram; a mesma verificação fixa de 0058 falha na linha 6 do
  script inline depois do upgrade.
- [Plans — Chromium](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361485/job/126747566562):
  os 72 casos pytest passaram, como os 72 do WebKit. O script posterior
  `browser_plans_responsive.py` repetiu o timeout de 30 segundos aguardando
  `.op-payment-body`, na mesma linha 134 e com a mesma stack da base.

### Instalação interrompida e tentativa resolvida

O [WebKit de Horários, tentativa 1](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361646/job/126747567253)
foi cancelado durante `playwright install --with-deps`, enquanto `apt` baixava
dependências de `azure.archive.ubuntu.com`. O job tem limite de 18 minutos;
nenhum comando pytest nem resumo de testes foi executado nessa tentativa.
O cancelamento foi registrado separadamente de uma falha funcional.

Somente os jobs falhos desse run foram repetidos, mantendo a mesma revisão.
A [tentativa 2, job 126760479943](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41879361646/job/126760479943)
passou com **16/16 em 54,71 s** e confirmou a mesma árvore do candidato. A
interrupção inicial ficou preservada no histórico, vinculada à sua resolução.
No pytest geral, a instalação de PostgreSQL/decoder levou 12 min 11 s; a suíte
posterior concluiu em 14 min 58 s, dentro do limite de 20 minutos da etapa.

O inventário reutilizável, os 62 resultados, as fases/multiplicidades e o
histórico das tentativas estão preservados no checkpoint de qualificação
`sent-messages-qualified-ci-baseline.json`, referenciado ao head `aaa6fa6` e à
produção `f58730ac`. Os 62 logs finais confirmaram o checkout de teste
`435095dc`; a API Git confirmou a mesma árvore no head, no checkout e no merge
publicado. A aprovação desta etapa mantém o passivo geral discriminado acima.

## Implantação em produção — concluída

Projeto Railway `strong-creation`, ambiente `production`. Os deployments
abaixo concluíram no commit `f58730ac8e380bd2c12286c69c1db3873d3cbf0f`.
Horários de 2026-11-04, em UTC:

| Serviço | Deployment | Resultado | Horário de SUCCESS |
|---|---|---|---|
| API | `0cb73c0a-79b9-46da-82f9-d97d809a4796` | `SUCCESS`, Online, 1/1 réplica | 18:13:39.258Z |
| Worker | `4a72b5ab-6011-4b0e-abcd-9f1ceaf05199` | `SUCCESS`, Online, 1/1 réplica | 18:13:39.092Z |
| Ponte WhatsApp | `7dce0b0f-15c2-44ac-9326-da5c577b8284` | `SUCCESS`, Online, 1/1 réplica | 18:12:38.871Z |

As conferências de estado registraram zero crashes, alertas ativos e falhas
recentes, sem deployment pendente desses serviços. A ponte confirmou `/health`
em 1/1 réplica às 18:12:37.186Z.

### Backup, migração e inicialização

Os logs da API confirmaram, em sequência:

1. Backup privado verificado às **18:13:22Z**.
2. Migração **0064 → 0065** às **18:13:23Z**.
3. Contrato de schema pronto às **18:13:24Z**.
4. Inicialização concluída às **18:13:35.755Z**.

O worker confirmou `subscription_sent_messages_schema_ready`, marcador
`0065_subscription_sent_messages`, às **18:13:25.747Z**, e iniciou suas funções
às **18:13:37Z**. A inicialização observada ocorreu depois da barreira de schema.
O registro preserva os resultados operacionais, sem caminhos de backup,
credenciais ou conteúdo de clientes.

## Conferência ao vivo e limites da evidência

No Chrome conectado, a tela publicada foi conferida até **18:18:08Z**:

- **Mensagens Enviadas** aparece no menu e abre os quatro registros existentes.
- As quatro tentativas têm resultado aceito pelo transporte; a apresentação
  mantém explícito que o recebimento não foi confirmado.
- O detalhe abriu somente para leitura, sem executar reenvio.
- O filtro de falhas reenviáveis resultou em lista vazia; os cartões globais
  mantiveram o total de quatro registros.
- Limpar os filtros restaurou os quatro registros.
- O histórico de áudio sem duração apareceu como **Não informado**.
- O console permaneceu sem erros durante a conferência.

A prova visual foi apresentada na conversa de trabalho. Destinatários,
telefones e conteúdo real não foram copiados para este registro.

O reenvio com confirmação, a resposta perdida seguida de recuperação por GET,
conflitos de versão, revogação, isolamento de conta/empresa e concorrência foram
exercitados nos ambientes sintéticos e no PostgreSQL real do CI. A inspeção em
produção permaneceu somente leitura: como não havia tentativa elegível no
filtro conferido, ela não valida um reenvio real para cliente. A aceitação pelo
transporte também não prova entrega ou leitura em aparelho físico.

Os cenários de navegador cobrem os engines e dimensões emulados registrados,
sem alegar teste em dispositivos físicos. A equivalência com o BotBot continua
limitada aos comportamentos descritos no [registro de observação](sent-messages-observation.md).
Histórico de ASR sem duração não foi reconstruído nem tratado como zero.

## Encerramento

Com CI específico, comparação geral, merge da árvore qualificada, três
deployments e conferência ao vivo concluídos, **a etapa 05 está encerrada**.

A observação de Diagnósticos (etapa 06) começou após essa última confirmação.
