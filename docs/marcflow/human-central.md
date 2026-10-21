# Central humana QR e Meta — desenvolvimento

Continuação na branch `codex/orion-marcflow-integrations-20261020`. A descrição
original sobre `4b1ba80` antecede a autorização operacional posterior.
O [recibo atual](revisao-manual-20261020/publication-receipt.json) distingue esse
histórico dos deployments realmente aplicados e dos testes isolados.

O próprio admin já foi provisionado como proprietário somente da demonstração,
com a IA desse cadastro ligada por aprovação explícita. A sessão QR principal
foi registrada e seu adaptador está pronto, com autenticação e volume originais.
Sua autoridade persistente foi adotada em E e o controle/fila humana habilitados
na API e worker em F. O novo login do próprio admin foi conferido em leitura,
incluindo controles disponíveis e proteção de envio sem atribuição. Os
[passos atuais](legacy-transition.md) usam o esquema 0030. Novo login do mesmo
admin é necessário para receber a identidade auditável, preservando a sessão
antiga. Meta e campanhas continuam desligados, sem mensagens reais de teste.
Esse aceite não homologa entrega ou resposta real da IA nem as demais integrações.

## Implementação

- O painel Orion integra lista e filtros de conversas, histórico paginado, assumir,
  aguardar cliente, pausar, encerrar e retomar IA. Usa a identidade auditável e a permissão
  atual do estabelecimento. Etiquetas e respostas rápidas reaproveitam o CRM existente.
- Contadores e leitura são pessoais na central; marcar como lida não envia recibos
  ao WhatsApp. A busca geral consulta cadastro e as últimas cem mensagens reais de
  cada conversa; a busca interna filtra apenas as mensagens carregadas. Há agrupamento
  por dia, paginação de histórico e atualização periódica sem recarregar a página.
- Mensagem recebida em conversa encerrada reabre atendimento humano sem responsável;
  em espera, devolve a situação a humano mantendo o atendente. Nenhuma dessas
  transições devolve automaticamente o controle à IA.
- `inbox_controls` passa a representar canais gerenciados QR e canais oficiais Meta.
  A migration 0027 preserva os registros QR e importa conservadoramente os handoffs
  Meta ativos. Pausas persistem até uma transição explícita; o timeout antigo não
  concede acesso à IA. O endpoint antigo de resolução deixa de contornar a central.
- O QR registra texto/ocorrência de áudio antes de interpretar, inclusive com IA
  desligada. O áudio tem indicação honesta de reprodução indisponível; não existe
  importação automática de histórico, mídia, agenda de contatos ou sessões antigas.
- Meta persiste o histórico junto do webhook assinado, antes do ACK e antes de
  apagar o texto transitório da fila antiga. O horário do provedor limita a janela
  de atendimento; payload sem horário verificável não libera envio de texto.
- Nova mensagem durante IA invalida gerações anteriores. A mesma autoridade é
  revalidada dentro das transações de negócio, ao reservar um envio e no bridge.
  IA desligada nunca impede o atendente de ler a conversa ou preparar atendimento.
- Pedidos explícitos de atendente são registrados já no ingresso autenticado,
  inclusive quando outro envio está pendente. A conversa passa para humano sem
  liberar o slot anterior. Na Meta, o pedido precede comandos de agenda; frases
  negadas não provocam transferência.
- Eventos Meta já pendentes antes de habilitar a central são copiados ao histórico
  antes de limpar a fila transitória. Isso não inventa horário do provedor nem
  renova a janela de envio de uma mensagem antiga.
- Envios humanos exigem posse pessoal, revisão atual e permissão válida, inclusive
  depois da fila. A identificação de operação impede duplicação após resposta HTTP
  perdida. A fila grava `sending` e o slot antes de qualquer chamada ao provedor.
- Reinício, timeout ou resultado desconhecido mantêm o slot bloqueado e nunca
  recolocam automaticamente a mensagem em fila. Recibos QR autenticados e webhooks
  Meta assinados atualizam aceitação, entrega e leitura separadamente. Recibos Meta
  anteriores à resposta HTTP são preservados; eventos atrasados não rebaixam leitura.
- Texto ainda em fila há mais de dez minutos é cancelado antes do transporte.
  Esse prazo não libera slots incertos nem retoma a IA: somente evita despachar
  uma intenção antiga depois de um longo período de pausa operacional.
- O worker Meta antigo não pode despachar mensagens sem revisão após a ativação
  da central. O piloto QR global fica impedido de enviar/acionar IA nesse modo:
  é necessário usar um canal gerenciado. Isso não desconecta nem transfere sessões.

## Ativação e recuperação

Todas as flags continuam `0` em `.env.example`. `INBOX_CONTROL_ENABLED` habilita
histórico e controle; `INBOX_TOOLS_ENABLED` habilita CRM; `INBOX_SEND_ENABLED` é uma
autorização independente para a fila. Meta exige também `META_LIVE_SEND=1`, canal
conectado, credencial válida e janela de 24 horas verificável. QR mantém allowlist,
lease, fence e autenticação privada já existentes. As flags de exemplo não
comprovam o estado efetivo; consultar o recibo dos processos publicados.

O ensaio original da central usava 0027 em banco descartável. No alvo atual,
seguir a sequência protegida de provisionamento, registro, adaptador, adoção e
habilitação em [legacy-transition.md](legacy-transition.md), com gate 0030.
As migrations possuem RLS forçada, FKs por
empresa e bloqueia downgrade com histórico ou controle Meta existente. Não reverter
apagando tabelas com dados. Pausar a fila conserva slots e histórico.

Após habilitar a central, manter `INBOX_CONTROL_ENABLED=1` enquanto houver
atendimentos sob seu controle. Para interromper transporte, usar as autorizações
da fila/canal/assistente. O QR principal adotado mantém a autoridade persistente
mesmo com controle global em `0`, desde que `QR_LEGACY_CENTRAL_ENABLED=1` permaneça
na API e no QR; esse valor global não pausa o atendimento nem desfaz a adoção.
Os demais canais ainda dependem de suas autorizações globais. Não usar essas
flags como substituto das transições explícitas de pausa ou controle.
`INBOX_SEND_ENABLED` controla a fila humana/Meta; o QR automático continua
dependendo da autorização da assistente do canal.

O botão de recibo do QR consulta somente o ledger autenticado: só resultados
`accepted` ou `refused/not_sent` liberam o slot correspondente. Meta pode recuperar
um resultado por webhook assinado com o identificador opaco da operação. Se não
houver correlação conclusiva, o atendimento permanece bloqueado. Não inventar
confirmação de entrega nem liberar um resultado incerto por tempo decorrido.

## Validação

A primeira CI da continuação (`008e0d9`, run `39446140306`) encontrou quatro
falhas: três consultas Meta tentavam `FOR SHARE` diretamente em `tenants`, cuja
permissão de UPDATE permanece revogada, e um teste QR herdava contexto de IA na
operação pessoal concorrente. A correção usa uma função boolean com escopo e
search_path fixos para manter o bloqueio na mesma transação sem ampliar grants.
O contexto pessoal é separado do contexto da geração, e o teste exige rejeição
da geração obsoleta desde a entrada da transação. Não foram removidas asserções.

## Contratos operacionais

Todos os endpoints públicos abaixo começam em
`/api/v1/tenants/{tenant_id}/management/inbox` e exigem autenticação e vínculo
vigente. O tenant da URL nunca substitui a autorização do servidor.

| Caminho | Ação |
|---|---|
| `GET /channels` | Metadados QR/Meta disponíveis no cadastro; não conecta sessão |
| `GET /conversations` | Filtros canal/situação/não lidas, busca e páginas de 50 |
| `GET /channels/{id}/messages` | Até 100 mensagens, cursor `before` da própria conversa |
| `POST /channels/{id}/read` | Marca somente a leitura da conta atual até uma mensagem existente |
| `GET/PUT /channels/{id}/control` | Leitura/transição com revisão esperada e conta pessoal |
| `GET /channels/{id}/control/history` | Últimos cem eventos de controle |
| `POST /channels/{id}/messages` | Enfileira texto revisado; UUID da operação identifica retries |
| `POST /channels/{id}/control/reconcile` | Consulta ledger QR; não envia e não presume resultado |

O worker consome apenas `queued`, revalida posse/versão/permissões e grava
`sending` e o slot antes do transporte. Estados `sending`/`uncertain` nunca voltam
automaticamente a `queued`. Com Meta desligada, textos ainda não tentados são
cancelados e não bloqueiam indefinidamente a varredura dos demais canais.

As rotas privadas QR usam chave do canal, owner, fence e lease corrente. Elas
registram ingresso/recibo e autorizam/finalizam o envio já associado ao controle;
não aceitam identidade de tenant, responsável ou credencial escolhida pelo modelo.

Fluxos usam `/api/v1/tenants/{tenant_id}/management/workflows`: listar/criar,
editar por ID e simular explicitamente uma revisão salva. Nenhuma rota de publicar
ou executar fluxo foi adicionada.

## Próxima fase: editor e simulação de fluxos

O editor reutiliza `workflow_definitions`, mantendo todos os fluxos desabilitados.
A migration 0028 adiciona revisão e horário de alteração. A gestão aceita até 50
rascunhos por empresa, com até dez etapas, edição e reordenação, CAS contra
sobrescrita e simulação explícita da versão salva. Tipos existentes: etiqueta,
atendimento humano e lembrete em rascunho. A simulação produz apenas uma trilha
de ações; não lê contatos, altera cadastros, cria jobs nem envia mensagens.

Execução distribuída, publicação de fluxos com condições e espera, mídia de
saída e campanhas continuam sendo fases posteriores. Esta etapa não apresenta
o editor de rascunhos como um executor de automações publicado.

Testes de domínio, integração PostgreSQL/RLS, bridge, DOM e navegador usam somente
empresas e destinatários sintéticos e transportes substituídos. A revisão final
`e896d2a` foi aprovada: [recibo e limites](human-central-validation.md).
O recibo anterior `final-validation.md` continua pertencendo exclusivamente ao
snapshot `b6650a0`.

Não houve teste com conta Meta, aparelho WhatsApp ou cliente real. Anexos, áudio de
saída e importação de histórico não fazem parte do envio de texto desta etapa.
O scanner profundo de segurança continua sem execução pelo bloqueio de perfil de
permissões já registrado; testes funcionais não equivalem a uma auditoria concluída.
