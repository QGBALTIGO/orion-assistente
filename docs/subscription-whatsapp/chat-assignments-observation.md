# Atribuições de Chat — observação da etapa 10

Data: 05/11/2026.

**Estado: pendente por falta de acesso ao contrato funcional da referência.**
A etapa 9, Mensagem em Massa, está encerrada na
[PR 262](https://github.com/QGBALTIGO/orion-assistente/pull/262), merge
`d17cb67a29fca8293299ac3b2b969601b540075a`. A etapa 10 não foi implementada
nem publicada; este registro não afirma equivalência funcional.

## O que foi observado no BotBot

- A [lista de Atribuições de Chat](https://botbot.chat/user/chat-assignment?channel=whatsapp)
  apresenta o título e o link **Criar Atribuição**, mas informa que o recurso
  não está disponível no plano da conta.
- O [link visível de criação](https://botbot.chat/user/chat-assignment/create?channel=whatsapp)
  retorna HTTP 403, com a mensagem `This action is unauthorized`.
- A ajuda disponível aponta para Telegram. O changelog e a documentação de API
  acessíveis não explicam o contrato desta função. O [site oficial](https://botbot.chat/)
  e a apresentação de preços apenas citam o nome do recurso.
- Não houve sondagem de rotas protegidas. Não foi contratado complemento,
  assinatura ou outro acesso pago.

Não foi possível observar campos, destinatário da atribuição, regras de
execução, edição, exclusão ou efeitos sobre as conversas. O nome do recurso
não permite concluir se ele atribui um chat a atendente, chatbot, dispositivo
ou outro destino.

## Componentes existentes no Orion

O mapeamento abaixo é uma inspeção do código existente, não um contrato
substituto para o recurso bloqueado.

| Componente confirmado | Capacidade existente e limite |
|---|---|
| [`app/inbox_control.py`](../../app/inbox_control.py) | Controle persistente por empresa/canal/telefone, responsável pessoal, revisão e eventos. Modos `ai`, `human`, `waiting`, `paused`, `closed`. O DTO `Change` permite assumir para si; não recebe um operador-alvo arbitrário. |
| [`app/inbox_conversations.py`](../../app/inbox_conversations.py) e [`app/inbox_runtime.py`](../../app/inbox_runtime.py) | Lista, histórico, leitura pessoal e fila humana com posse, autorização e recibos. Não implementam distribuição automática ou transferência para outra conta por um campo de destino. |
| [`app/subscription_chatbot_store.py`](../../app/subscription_chatbot_store.py) e [`app/subscription_devices.py`](../../app/subscription_devices.py) | Guardas de proprietário/nicho, transações, dispositivo, geração, lease/fence e eventos/efeitos persistidos. O stack de assinaturas mantém a assistente e a Central legadas separadas. |
| [`app/subscription_chatbot_engine.py`](../../app/subscription_chatbot_engine.py) | `record_event`, `drain_contact`, `policy_allows` e `authorize_effect` são pontos de decisão do chatbot. Atualmente não consultam atribuição humana por conversa. |
| [`app/workspace_policy.py`](../../app/workspace_policy.py) e [`app/plan_routing.py`](../../app/plan_routing.py) | O nicho possui funcionalidades e rotas explicitamente permitidas. As rotas da Central padrão não estão liberadas nesse workspace. |

O reaproveitamento direto da Central também encontra diferenças concretas:

- Os controles da Central aceitam telefone brasileiro `+55`; os DTOs de
  assinaturas aceitam E.164 internacional em
  [`app/subscription_device_schemas.py`](../../app/subscription_device_schemas.py).
- A Central opera com `app_runtime`; as tabelas de dispositivos usam
  `app_support`, conforme as migrations
  [`0025`](../../alembic/versions/0025_inbox_control.py),
  [`0027`](../../alembic/versions/0027_unified_inbox.py) e
  [`0062`](../../alembic/versions/0062_subscription_devices.py).
- O ingresso de assinaturas não registra respostas manuais comuns do aparelho
  como atendimento: [`qr-bridge/src/chatbot-input.mjs`](../../qr-bridge/src/chatbot-input.mjs)
  aceita `fromMe` somente como comando humano com `!`, excluindo mensagens
  geradas pelo sistema.
- Identidades de operadores vêm de contas e vínculos vigentes, não de contatos
  de clientes. A gestão existente desses vínculos é reservada a admin/suporte
  em [`app/business_profile_api.py`](../../app/business_profile_api.py).

## Questões necessárias para definir o contrato

1. Qual entidade recebe a atribuição: conta, equipe, telefone, chatbot,
   dispositivo ou integração?
2. A unidade atribuída é uma conversa existente, um contato, um grupo ou uma
   regra para novas conversas? O vínculo pertence a um dispositivo específico?
3. Quais campos, filtros, estados, limites e ações de edição/exclusão existem?
4. A criação é manual ou automática? Há transferência, desatribuição ou prazo?
5. A atribuição apenas organiza a lista ou também pausa respostas, muda regras,
   encaminha mensagens ou notifica alguém?
6. Como são tratados responsável anterior, mudanças concorrentes, histórico
   e envios já em andamento?

## Continuidade

Em 2026-11-05, o usuário autorizou explicitamente manter Atribuições de Chat
pendente e continuar até o fim dos módulos acessíveis, sem pagar por
complementos ou acesso e sem pedir nova confirmação por bloqueios de plano.
A observação de Enviar Mensagem, etapa 11, foi iniciada sob essa autorização.
Os módulos acessíveis seguem um de cada vez, com observação, implementação,
validação e publicação antes de avançar ao próximo módulo acessível.

A etapa 10 continua sem implementação ou publicação. A autorização de
continuidade não esclarece os campos ou efeitos desconhecidos e não comprova
equivalência funcional. As questões acima ficam registradas para quando
houver uma referência acessível; essa pendência não impede o andamento dos
demais módulos. Não se presume que a Central existente corresponda ao
comportamento do BotBot. O início da observação da etapa 11 também não é
uma declaração de implementação ou publicação dessa função.
