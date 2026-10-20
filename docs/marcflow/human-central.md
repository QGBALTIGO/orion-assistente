# Central humana QR e Meta — desenvolvimento

Continuação na branch `codex/orion-marcflow-integrations-20261020`, sobre `4b1ba80`.
Nenhum deploy, migration operacional, conexão de sessão ou envio real foi executado.

## Implementação

- O painel Orion integra lista e filtros de conversas, histórico paginado, assumir,
  aguardar cliente, pausar, encerrar e retomar IA. Usa a conta pessoal e a permissão
  atual do estabelecimento. Etiquetas e respostas rápidas reaproveitam o CRM existente.
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
- Envios humanos exigem posse pessoal, revisão atual e permissão válida, inclusive
  depois da fila. A identificação de operação impede duplicação após resposta HTTP
  perdida. A fila grava `sending` e o slot antes de qualquer chamada ao provedor.
- Reinício, timeout ou resultado desconhecido mantêm o slot bloqueado e nunca
  recolocam automaticamente a mensagem em fila. Recibos QR autenticados e webhooks
  Meta assinados atualizam aceitação, entrega e leitura separadamente. Recibos Meta
  anteriores à resposta HTTP são preservados; eventos atrasados não rebaixam leitura.
- O worker Meta antigo não pode despachar mensagens sem revisão após a ativação
  da central. O piloto QR global fica impedido de enviar/acionar IA nesse modo:
  é necessário usar um canal gerenciado. Isso não desconecta nem transfere sessões.

## Ativação e recuperação

Todas as flags continuam `0` em `.env.example`. `INBOX_CONTROL_ENABLED` habilita
histórico e controle; `INBOX_TOOLS_ENABLED` habilita CRM; `INBOX_SEND_ENABLED` é uma
autorização independente para a fila. Meta exige também `META_LIVE_SEND=1`, canal
conectado, credencial válida e janela de 24 horas verificável. QR mantém allowlist,
lease, fence e autenticação privada já existentes. Não ativar em produção neste trabalho.

Atualizar API, worker e bridge em conjunto, aplicar 0027 em banco descartável e
homologar antes de planejar produção. A migration possui RLS forçada, FKs por
empresa e bloqueia downgrade com histórico ou controle Meta existente. Não reverter
apagando tabelas com dados. Pausar a fila conserva slots e histórico.

O botão de recibo do QR consulta somente o ledger autenticado: só resultados
`accepted` ou `refused/not_sent` liberam o slot correspondente. Meta pode recuperar
um resultado por webhook assinado com o identificador opaco da operação. Se não
houver correlação conclusiva, o atendimento permanece bloqueado. Não inventar
confirmação de entrega nem liberar um resultado incerto por tempo decorrido.

## Validação

Testes de domínio, integração PostgreSQL/RLS, bridge, DOM e navegador usam somente
empresas e destinatários sintéticos e transportes substituídos. Resultados finais
serão registrados após a qualificação da revisão final. O recibo anterior em
`final-validation.md` continua pertencendo exclusivamente ao snapshot `b6650a0`.

Não houve teste com conta Meta, aparelho WhatsApp ou cliente real. Anexos, áudio de
saída e importação de histórico não fazem parte do envio de texto desta etapa.
O scanner profundo de segurança continua sem execução pelo bloqueio de perfil de
permissões já registrado; testes funcionais não equivalem a uma auditoria concluída.
