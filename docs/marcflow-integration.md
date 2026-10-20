# MarcFlow → Orion: execução incremental de desenvolvimento

Data: 20/10/2026. PR de desenvolvimento: https://github.com/QGBALTIGO/orion-assistente/pull/80.

**A missão completa permanece PARCIAL.** A continuação entrega a central humana
de texto com QR gerenciado e Meta, histórico, leitura pessoal, CRM, respostas
rápidas, controle único e fila persistente. Também avança o editor e simulador
de rascunhos lineares. O executor distribuído de fluxos, a mídia de atendimento e
as demais extensões ainda não estão concluídos. Nenhum recurso foi habilitado em produção.

O estado atual, os contratos e os limites da central estão em
[human-central.md](marcflow/human-central.md). Os recibos `validation.json` e
`final-validation.md` abaixo preservam a qualificação da etapa anterior; não
devem ser interpretados como testes desta continuação.

## 1. Alvo e preservação

- Orion principal confirmado: `QGBALTIGO/orion-assistente`, FastAPI, PostgreSQL,
  RLS, Alembic, Arq/Redis, frontend HTML/JavaScript e bridge Baileys separado.
- `main` remoto consultado: `24456e82155609bcb8cde2c72d4aac3612a91b54`.
- Base de desenvolvimento preservada: `codex/whatsapp-owner-shortcut`,
  `6d9c971f5cebc9b652960f765b2f98ec5678f2eb`. Seus oito commits posteriores à
  `main` correspondem ao conteúdo da cópia local mais recente, que usava hashes
  diferentes. A comparação das árvores não encontrou diferença de conteúdo.
- Trabalho isolado em `codex/orion-marcflow-integrations-20261020`, com PR em
  rascunho direcionado à base de desenvolvimento. Não houve merge na `main`.
- Repositório antigo `QGBALTIGO/ORION`: `4c08e3c5cd774a56165e2a92bceb4bf425130c4d`.
  A cópia local já tinha alterações; foi preservada e não recebeu a integração.
- Referência MarcFlow: `aa105ccd21a5adb8b5db342e884f8219fc7d1bc7`. A cópia de
  referência continua sem alterações rastreadas. Não foi iniciada sua aplicação.
- Não houve acesso a dados operacionais, alteração de credenciais, migração em
  Railway, conexão/desconexão de WhatsApp, envio a contatos ou deploy.

O envio Git local ficou preso no gerenciador de credenciais. Os commits foram
publicados pela conexão autenticada do GitHub; as árvores remota e local foram
comparadas e a branch local foi alinhada sem sobrescrever arquivos existentes.

## 2. Auditoria: evidência e limites

O [inventário por arquivo](marcflow/file-inventory.csv) registra caminho, revisão,
linhas e SHA-256 de todos os arquivos de texto rastreados das duas árvores.
O [resumo](marcflow/inventory-summary.json) identifica as revisões inventariadas.
O MarcFlow contém 343 arquivos rastreados, todos inventariados como texto; o
snapshot Orion tinha 502 arquivos rastreados e 490 arquivos de texto.

**Inventário automatizado não significa revisão manual integral.** A leitura
aprofundada concentrou-se em chat, respostas, etiquetas, fluxos, campanhas,
recorrência, webhooks, áudio, arquivo, persistência, permissões, canais, recepção
QR, controle humano e notificações da agenda. Não foi feita homologação visual
do aplicativo MarcFlow, nem execução de seus serviços MySQL/WhatsApp.

Constatações verificadas no código de referência:

1. `src/modules/whatsapp/scheduler.ts` seleciona mensagens `PENDING`, envia e muda
   para `SENT`. Não calcula uma próxima ocorrência a partir de `cronExpression`.
   A UI `src/app/dashboard/scheduler/page.tsx` permite configurar recorrência.
   Portanto, essa recorrência não deve ser transportada como executor completo.
2. `src/modules/broadcast/runner.ts` mantém destinatários no banco e permite
   pausa/retomada, mas seu conjunto `running` é local ao processo. A seleção de
   um destinatário pendente seguida do envio não substitui arbitragem distribuída.
   A opção `skipOptedOut` também não equivale ao consentimento verificável Orion.
3. `src/modules/flows/engine.ts` contém snapshots de grafo, espera persistida e
   claim condicional no `tick`. Há temporizador local e fila por contato no
   processo. Aproveitar a modelagem não autoriza reutilizar sua execução como
   coordenação única entre várias instâncias.
4. `src/modules/flows/logic.ts` substitui certas variáveis desconhecidas por
   vazio. O novo renderizador Orion recusa essas variáveis e fatos ausentes.
5. `src/modules/quick-replies/run.ts` executa várias ações e esperas em memória.
   Nesta entrega, sequências e envio de mídia não foram ativados parcialmente.
6. `src/lib/webhook.ts` assina somente quando existe segredo e usa `fetch` com
   timeout. Esses trechos não bastam para cumprir HMAC obrigatório, validação DNS,
   bloqueio de destinos reservados e política de redirects exigidos na missão.
7. `src/modules/whatsapp/voice.ts` usa ffmpeg e repack Opus. Nenhuma vantagem
   sobre o Orion em iPhone físico foi comprovada; o áudio existente foi mantido.
8. O arquivo exportável do MarcFlow usa histórico persistido e registra mídias
   indisponíveis. Não implica recuperação garantida do histórico WhatsApp.

Testes puros do MarcFlow executados sem conexão externa:

- `scripts/test-flow-logic.ts`: `flow logic: all checks passed`.
- `scripts/test-quick-replies.ts`: `quick reply logic: ok`.

Não foram executados os testes que precisam de MySQL, sessões ou mídias reais.

## 3. Arquitetura resultante

O PostgreSQL continua sendo a autoridade operacional. O novo CRM consulta
`customers`, `appointments`, `appointment_items`, `professionals`, o perfil
publicado da empresa e as preferências do site. Ele não cria clientes a partir
de números digitados ou da agenda do telefone. Agendamentos continuam passando
pelos serviços existentes e pelas constraints do banco.

O frontend adiciona um módulo JavaScript ao painel atual, com os tokens de cores,
Figtree/Outfit, componentes e temas do Orion. Não adiciona React, Next.js,
Prisma, MySQL, outro painel ou dependência de produção.

### Etiquetas e respostas — IMPLEMENTADO E TESTADO em desenvolvimento

- Criar, editar, excluir e pesquisar etiquetas; associar/remover no cliente;
  pesquisar clientes por nome, telefone e etiqueta, em páginas de 50 registros.
- Uma associação repetida é idempotente. FKs compostas impedem vínculos entre
  empresas. Nomes de etiqueta são únicos por empresa sem distinção de caixa.
- Respostas compartilhadas ou pessoais, categoria, busca, edição, exclusão e
  duplicação pelo formulário. Limite de 200 etiquetas e 200 respostas por empresa.
- As respostas pessoais são filtradas pela conta autenticada em todas as
  operações de leitura, edição, exclusão e prévia.
- Edição/exclusão usam versão esperada. Uma edição concorrente recebe conflito.
- Prévia busca os fatos atuais do cadastro e do próximo agendamento confirmado.
  Data/hora usam o fuso da empresa. Endereço vem apenas do perfil publicado.
- Variável inexistente, sintaxe inválida, fato ausente ou texto acima do limite
  impedem o preparo. Não existe substituição silenciosa por informação inventada.
- A biblioteca prepara e copia texto para revisão. A central permite levar a
  resposta preparada ao compositor e solicitar envio humano após assumir a
  conversa. Sequências e mídias continuam fora desta implementação.
- Histórico do contexto é limitado aos 20 agendamentos mais recentes. A consulta
  do próximo agendamento é independente desse recorte.
- Etiquetas não alteram consentimento, assinatura, valores, reservas ou canais.

Flag: `INBOX_TOOLS_ENABLED=0` por padrão. A tela mostra preparação quando desligada.

### Arbitragem — integrada à central humana QR/Meta em desenvolvimento

`inbox_controls` usa chave `(tenant_id, channel_id, contact_e164)` e estados
`ai`, `human`, `paused`, `waiting`, `closed`. Não há reativação automática por
expiração. A alteração exige conta pessoal, permissão vigente e revisão esperada.
O responsável e cada transição ficam persistidos em `inbox_control_events`.

Antes de gerar uma resposta, o QR gerenciado captura a revisão. Operações de
negócio verificam essa revisão sob lock compartilhado dentro da mesma transação.
A mudança de controle precisa do lock exclusivo: uma operação já em transação
termina antes da transferência; operações posteriores precisam da nova revisão.
Não se mantém esse lock durante chamadas ao modelo.

Após a devolução manual à IA, somente o estado transitório antigo de handoff é
limpo no próximo turno autorizado. Estados de confirmação/reserva que não são
handoff permanecem intactos. A expiração do Redis não reativa o controle humano
persistente.

Antes do transporte, o bridge solicita uma reserva persistente de envio. Uma
conversa em estado humano/suspenso ou com envio pendente recusa novos envios.
Na continuação, essa recusa é aplicada aos envios automáticos; o envio humano
exige a posse pessoal e a operação manual reservada pela mesma autoridade.
Uma falha incerta conserva a reserva, inclusive após reinício. A reserva não
expira automaticamente. A liberação exige resultado aceito persistido ou consulta
de recibo conclusivo; aceitação continua distinta de entrega/leitura.

A ativação é transmitida pela concessão do canal obtida da API. Assim, o conector
atualizado aplica o controle mesmo sem uma flag local equivalente. Com backend
inacessível, não prossegue com um envio protegido.

**Continuação e limites:**

- A central integra QR gerenciado e Meta com assumir, devolver, aguardar, pausar,
  encerrar, histórico, fila e envio humano sob a mesma reserva. A migration 0027
  incorpora os handoffs Meta ativos sem permitir reativação por timeout.
- O piloto QR global é preservado, mas não pode enviar nem acionar IA com a
  central habilitada. Usar um canal gerenciado exige homologação explícita;
  nenhuma sessão antiga é transferida ou desconectada automaticamente.
- A retomada manual pode remover a pausa comercial legada somente se não houver
  outra conversa suspensa para o mesmo contato. Não concede opt-in comercial.
- Mídias, reprodução de áudio, citação, encaminhamento e reações não são
  apresentados como recursos prontos da central gerenciada de texto.
- Resultado incerto sem recibo conclusivo continua suspenso. Não existe botão
  para afirmar artificialmente que não houve envio ou para repetir às cegas.
- O estado `flow` fica reservado para quando houver executor integrado; não foi
  apresentado como funcionalidade operacional nesta entrega.

Flags: `INBOX_CONTROL_ENABLED=0`, `INBOX_SEND_ENABLED=0`. Homologar API, worker
e bridge juntos em canal sintético; Meta exige também seu opt-in de transporte.

### Notificação de cancelamento — IMPLEMENTADO, AGUARDANDO HOMOLOGAÇÃO

O novo evento operacional `cancelled` reutiliza `automation_jobs`, o planejador,
o worker, as regras revisadas e os recibos existentes. O gatilho vem de
`appointment_history` e do estado `cancelled` real. O protocolo foi incluído entre
as variáveis factuais. `cancelled_7d` continua sendo uma finalidade comercial
separada, sujeita a autorização comercial.

O aviso exige a configuração revisada pelo proprietário, consentimento
operacional, canal autorizado, versões atuais e a janela de horário existente.
Não ativa regras, consentimentos ou envios por padrão. Cancelamento ou remarcação
invalidam os lembretes antigos pela verificação da versão/estado antes de enviar.
Não houve envio real para provar entrega ou leitura.

## 4. Migrations e contratos

| Migration | Alterações | Rollback |
|---|---|---|
| `0024_inbox_tools` | `inbox_labels`, `inbox_customer_labels`, `inbox_quick_replies`, índices/FKs/RLS | Recusa exclusão se houver dados |
| `0025_inbox_control` | `inbox_controls`, `inbox_control_events`, função booleana de verificação de contato/canal, RLS e grants mínimos | Recusa exclusão se houver controles |
| `0026_cancelled_notification` | Amplia constraint do evento da fila existente | Recusa rollback com regras/jobs de cancelamento |

As novas tabelas aplicam `ENABLE` e `FORCE ROW LEVEL SECURITY`. O runtime não recebe
credenciais do canal nem acesso geral a `allowed_senders`; a função retorna somente
um booleano no contexto do tenant da transação. Eventos de controle são somente
inseridos/lidos pelo runtime. Nenhuma migration foi executada em banco operacional.

Prefixo autenticado `P=/api/v1/tenants/{tenant_id}/management/inbox`:

| Método | Caminho | Operação |
|---|---|---|
| GET | `P/capabilities` | Disponibilidade real e limites do recurso |
| GET, POST | `P/labels` | Biblioteca de etiquetas |
| PUT, DELETE | `P/labels/{id}` | Editar/excluir com versão esperada |
| GET | `P/customers` | Busca/paginação e filtro por etiqueta |
| GET | `P/customers/{id}` | Contexto autorizado do cadastro/agenda |
| PUT, DELETE | `P/customers/{id}/labels/{label}` | Associar/remover sem duplicar cliente |
| GET, POST | `P/quick-replies` | Biblioteca pessoal/compartilhada |
| PUT, DELETE | `P/quick-replies/{id}` | Editar/excluir com versão |
| POST | `P/quick-replies/{id}/preview` | Preparo factual, sem envio |
| GET, PUT | `P/channels/{channel}/control` | Consultar/alterar controle persistente |
| GET | `P/channels/{channel}/control/history` | Últimas 100 transições |
| POST | `P/channels/{channel}/control/reconcile` | Consulta de recibo sem reenvio |

Os endpoints privados `send-authorize` e `send-finish` usam autenticação de canal,
remetente verificado, proprietário, fencing e validade da concessão existentes.
Não são credenciais entregues ao navegador. O OpenAPI público inclui os contratos
autenticados; as rotas internas permanecem excluídas.

## 5. Matriz MarcFlow → Orion

| Recurso | MarcFlow | Orion anterior | Orion após trabalho | Evidência | Pendência |
|---|---|---|---|---|---|
| Arquitetura | Next/Prisma/MySQL | FastAPI/PG/Redis | Mantida | `app/main.py`, `pyproject.toml` | Nenhuma substituição proposta |
| Chat | Socket, lista e mídia | Caixa limitada do piloto | Central QR/Meta de texto, histórico, contadores e leitura pessoal | `inbox_conversations.py`, `orion-conversations.js` | Mídia e homologação real |
| Identidade | JID/LID e contatos | Resolução verificada existente | Reutilizada | `core.mjs`, `customer_names.py` | Homologação real por provedor |
| CRM no atendimento | Dados WhatsApp | Clientes/agenda reais | Consulta integrada na nova tela | `inbox_api.context` | Fidelidade, profissional habitual e ligação à conversa |
| Etiquetas | Label/ChatLabel | Sem biblioteca equivalente | CRUD e associação ao cliente | `0024`, testes PostgreSQL/DOM | Etiquetas da conversa e segmentação de campanha |
| Respostas rápidas | Sequências de ações | Sem biblioteca equivalente | Texto factual pessoal/compartilhado | `inbox_templates.py`, UI | Sequências duráveis/mídias/progresso |
| Handoff | Pausa do contato | Pausas separadas | Controle QR/Meta, UI e envio humano com geração e slot únicos | `0025`, `0027`, `inbox_runtime.py` | Homologação; piloto não migra sessões |
| Recibos | Eventos Baileys | Ledger/diagnóstico existentes | Reserva antes do transporte | `channel-manager.mjs` | WhatsApp real, reinício e contingência operacional |
| Editor visual | XYFlow | Workflow linear desativado | Editor linear com reordenação e CAS | `orion-flows.js`, `workflow_drafts.py` | Grafo, publicação e executor |
| Simulador de fluxos | Contato artificial | Prévia isolada da IA | Trilha explícita da versão salva sem ações reais | `workflow_drafts.simulate` | Condições e grafo |
| Versionamento de fluxo | Draft/published e snapshot | Perfis versionados; workflow básico | Revisões de rascunho com CAS e auditoria | `0028_workflow_drafts` | Snapshots imutáveis por execução |
| Condições/gatilhos | Regras e blocos | Interpretação e eventos de domínio | Mantidos | `automation_policy.py` | Gatilhos de fluxo distribuído |
| Confirmação de agenda | Não é motor de agenda Orion | Fila revisada já existente | Preservada; variável protocolo | `automation_runtime.py` | Homologar no canal escolhido |
| Remarcação | Fluxos genéricos | Evento/versão já existentes | Preservados | `appointment_lifecycle.py` | Homologação operacional |
| Cancelamento imediato | Ações genéricas | Só recuperação comercial na fila | Novo evento operacional | `0026`, testes da política | Homologar com regra revisada |
| Lembretes | Agendamento genérico | 24h/2h/1h com invalidação | Preservados | `delivery_reason`, `schedule_for` | Intervalos personalizáveis além dos existentes |
| Não comparecimento | Sem autoridade Orion | Registro de ausência | Preservado | `management_api.py` | Acompanhamento autorizado |
| Fidelidade/pós-serviço | Ações genéricas | Recompensas e rotinas existentes | Preservados | `loyalty_rewards.py`, runtime | Homologação e apresentação no CRM |
| Marketing IA | Composição e transmissão | Texto IA, revisão e fila existentes | Preservados | `management_ai.py`, `automation_api.py` | Segmentação por etiqueta/mídia e UX completa |
| Campanhas | Destinatário persistido; runner local | Aprovação, consentimento e ledger | Mantidos | `automation_runtime.py` | Duplicação/arquivo, importação revisada, mídias |
| Recorrência | UI cron; executor sem próxima ocorrência | Rotinas específicas persistidas | Sem copiar recorrência incompleta | `scheduler.ts` | NÃO INICIADO: contrato geral de recorrência |
| AutoReply | Palavra, escopo, prioridade | IA e regras de domínio | Mantidos | `store/autoreply.ts`, `qr_ai.py` | Regras configuráveis com árbitro comum |
| Webhooks configuráveis | Segredo opcional e fetch | Integrações específicas autenticadas | Não ampliados | `src/lib/webhook.ts` | NÃO INICIADO: HMAC, SSRF/DNS e outbox |
| Exportação de conversas | ZIP e relatório | CSVs operacionais | Preservados | `csv_export.py` | Jobs de ZIP privado, retenção e ACL |
| Biblioteca de mídia | Assets locais | Upload/armazenamento existentes | Preservados | `media_api.py` | Mídia privada de atendimento e ACL |
| Voz/iPhone | ffmpeg e repack | Áudio/ASR e testes existentes | Não substituídos | `voice.ts`, `qr_channel_audio.py` | Comparação física Android/iPhone/Web |
| Sessões | Persistência Prisma e manager | Canais, fence, chaves cifradas | Proteção de envio ampliada | `qr_channels.py` | Não tocar sessões reais nesta missão |
| Notificações internas | Alertas próprios | Alertas persistidos da agenda | Preservados | `orion-suite.js` | Alertas humanos/desconexão por canal |
| Monitoramento | Informações de sistema | Health/métricas/diagnóstico restritos | Preservados | `ops_api.py`, `metrics_api.py` | Filas/métricas de fluxos futuros |
| Planos | Modelo próprio | Assinaturas existentes | Sem alteração comercial | `commercial_api.py` | Definir direitos quando os recursos amadurecerem |
| Grupos/figurinhas | Ferramentas amplas | Fora do núcleo da agenda | NÃO RECOMENDADO nesta etapa | Complexidade e prioridade da missão | Reavaliar só com caso comercial concreto |

## 6. Testes e critérios de leitura

Resultados iniciais reproduzidos antes das novas mudanças de comportamento:

| Ambiente | Resultado |
|---|---|
| Python local, base original | 3.732 passaram, 387 ignorados, 4 falharam |
| Mesma suíte local na cópia inicial de trabalho | Mesmo conjunto de quatro falhas |
| Causa dessas falhas | URLs de PostgreSQL/suporte/migration ausentes |
| Interface existente | 291 passaram |
| Conector existente | 406 passaram, 4 ignorados |

Cobertura acrescentada: variáveis inválidas/ausentes, limite UTF-16, texto não
executável, API desativada, autenticação, RLS real, FK entre empresas, respostas
pessoais, ausência de agendamento, idempotência de associação, CAS entre operadores,
pausa persistente, envio incerto sem expiração, recusa de transporte após handoff,
falha de salvamento, troca de empresa e preview sem envio.

`tests/integration/test_inbox_migration_postgres.py` cria um banco local aleatório,
aplica todas as migrations, regride a `0023`, reaplica e prova que rollback com
dados é recusado. Ele recusa alvo não local e não usa o banco operacional.

`ui-tests/inbox-browser.mjs` executa Chromium em 360/768/1440 px, claro/escuro,
com dados sintéticos e toda requisição externa bloqueada. Foram aprovados seis
cenários, sem overflow horizontal, erros JavaScript ou solicitação de envio.
Esses testes comprovam a interface com APIs sintéticas; não são homologação com
login de usuário real, banco operacional, telefone físico ou WhatsApp.

A CI de PostgreSQL/Redis/Docker é executada no PR. Os IDs, contagens finais e
resultados do snapshot de código qualificado ficam no registro de validação desta
entrega, junto da distinção entre testes locais e infraestrutura real descartável.

O [recibo final](marcflow/final-validation.md) qualifica o código `b6650a0`:
4.149 aprovações na suíte PostgreSQL/Redis, 4.038 no Compose, 413 no conector
Linux e 298 nos testes DOM. Todos os trabalhos da CI correspondente passaram.
Os conjuntos possuem casos sobrepostos e suas contagens não devem ser somadas
como testes únicos. As limitações de homologação real permanecem.

## 7. Ordem de continuação e ativação segura

1. **FASE 0 — PARCIAL:** completar os 25 itens de avaliação por recurso, leitura
   manual remanescente e revisão exaustiva da superfície de segurança. O inventário
   atual e as constatações acima são evidência parcial, não uma certificação.
2. **FASE 1 — núcleo humano de texto implementado:** QR/Meta, histórico, paginação,
   leitura pessoal, interface e envio humano com arbitragem persistente. Restam
   mídias, fidelidade/contexto ampliado e homologação real autorizada.
3. **FASE 2 — PARCIAL:** homologar eventos existentes e cancelamento em canal de
   teste autorizado; provar concorrência com remarcação/cancelamento, recebimento
   durante envio, reinício e recibo perdido, sem alegar entrega pelo aceite.
4. **FASE 3 — INICIADA:** editor linear e simulador de rascunhos com limites e
   controle de versão reutilizam `workflow_definitions`. Restam grafo, snapshots,
   executor Arq e publicação exclusiva do proprietário.
5. **FASE 4 — PARCIAL anterior:** reaproveitar campanhas/consentimentos/worker
   existentes; acrescentar segmentação por etiqueta, mídia privada e controles de
   edição, agendamento, pausa e retomada sem duplicação ou consentimento presumido.
6. **FASE 5 — NÃO INICIADO:** só depois das bases, jobs de exportação e biblioteca
   privada; webhooks com assinatura obrigatória e transporte resistente a SSRF;
   medições de áudio antes de qualquer substituição do codec.
7. **FASE 6 — PARCIAL:** repetir isolamento/concorrência sobre cada integração,
   validar desempenho com volume por tenant, acessibilidade, direitos por conta,
   dados de upgrade/rollback e homologação real explicitamente autorizada.

Antes de qualquer publicação: revisar o PR, conferir backup e restore em ambiente
descartável, confirmar as migrations e privilégios com o papel runtime real,
planejar backend/bridge compatíveis e manter as flags desligadas. A ativação
externa depende de autorização posterior do proprietário e homologação por canal.
Não há comando de deploy automático nesta entrega.

Definir também retenção e exclusão autorizada dos novos controles, eventos,
etiquetas e respostas pessoais antes de ativar o recurso com dados reais.

## 8. Licenças e atribuição

Referência conceitual: [MarcFlow](https://github.com/marcolamaia/MarcFlow), Marcos
Maia (@marcolamaia) e Aditya, licença MIT no snapshot consultado. Não foi copiado
código-fonte nem adicionado pacote de produção do MarcFlow. Se código substancial
for reutilizado futuramente, incluir os avisos completos do respectivo arquivo e
de suas dependências na distribuição.

O lockfile contém licenças além de MIT, incluindo metadados LGPL/MPL/GPL e quatro
entradas sem licença declarada. A contagem está em `inventory-summary.json`.
O campo `license: MIT` do projeto não caracteriza todas as dependências.
A revisão de licenças transitivas/binárias não está concluída; por isso nenhuma
dessas dependências foi transplantada nesta etapa.

## 9. Varredura automatizada de segurança — BLOQUEADO

A tentativa de Deep Security Scan não iniciou os workers. O plugin retornou:

`Deep Scan cannot safely start a read-only worker: the parent must provide a managed filesystem permission profile.`

O ambiente desta execução oferece acesso irrestrito, sem o perfil gerenciado de
leitura exigido pelo scanner. Não foram alteradas as permissões do ambiente.
Não existe resultado de varredura completa nem conclusão de ausência de
vulnerabilidades. A inspeção de código, os testes de RLS/permissões e as
constatações parciais acima permanecem evidências de escopo limitado. Retomar essa
varredura exige uma execução com o perfil de permissões compatível.

## 10. Arquivos e commits

O [manifesto de alterações](marcflow/change-manifest.json) lista arquivos,
contratos HTTP, tabelas, migrations, flags e dependências da implementação.

| Commit | Incremento |
|---|---|
| `8611d56` | Backend CRM, arbitragem e aviso de cancelamento |
| `5314880` | Etiquetas e respostas factuais no painel |
| `84e32bc` | Eventos de operadores, rollback protegido e navegador responsivo |
| `f302bd2` | Matriz, inventário, HTTP e concorrência com PostgreSQL |
| `19bb6ea` | Retomada pela autoridade persistente e configuração sintética do canal |
| `9e6accc` | Revalidação concorrente sem promover locks de leitura a escrita |
| `b09b313` | Gravações pendentes durante troca de empresa |
| `b6650a0` | Verificação atômica das flags antes da reserva de envio |

O recibo da primeira qualificação foi preservado em [validation.json](marcflow/validation.json).
O recibo final está em [final-validation.md](marcflow/final-validation.md).
Um commit posterior de documentação pode registrar esses recibos sem alterar o
código qualificado indicado no arquivo.

## 11. Continuação: central humana QR/Meta e rascunhos

A mesma branch recebeu a central de texto, histórico/leitura pessoal, envio
humano com posse/revisão/slot persistentes, ingresso e recibos QR/Meta e bloqueio
da outbox antiga após ativação. Foram preservados eventos Meta já pendentes e
pedidos humanos recebidos durante outro envio. O editor/simulador linear
reaproveita `workflow_definitions` e mantém execução desabilitada.

Código qualificado da continuação: `e896d2a31b464bf1ed7b7a1fe0789782bb9cf5b5`.
CI aprovada: 4.188 testes PostgreSQL/Redis (1 ignorado), 4.077 em Compose
(112 ignorados), 416 do bridge e 306 DOM. Não somar conjuntos sobrepostos.

- [Implementação, contratos e limitações](marcflow/human-central.md).
- [Manifesto da continuação](marcflow/human-central-manifest.json).
- [Recibo da revisão final](marcflow/human-central-validation.md).

São duas migrations adicionais (0027/0028), quatro tabelas novas e versionamento
da tabela de fluxos existente, sem dependência nova de produção. O opt-in
`INBOX_SEND_ENABLED=0` foi acrescentado. Nenhum deploy ou recurso real foi ativado.
