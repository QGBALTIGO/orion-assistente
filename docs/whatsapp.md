# FATIA 3 — Integração Meta WhatsApp Cloud API (em validação)

O projeto não recebe nenhum número real nesta entrega. Para utilizar a API
oficial, será necessário App Meta autorizado, número elegível/coexistência
validada, App Secret, Verify Token e token de acesso por canal criptografado.
Não instale dependências de QR Code não oficiais para este fluxo.

## Verificação e roteamento
GET /webhooks/whatsapp valida `hub.verify_token` e responde challenge.
POST valida assinatura HMAC-SHA256 de bytes brutos via cabeçalho
`X-Hub-Signature-256`, então extrai metadados Meta e resolve o estabelecimento
exclusivamente pelo identificador externo registrado `phone_number_id`.
Eventos com identificador desconhecido são ignorados, não redirecionados.
O banco persiste eventos antes do HTTP 200, com chave única de idempotência.
Um worker Arq processa filas Redis e revarre pendências persistidas.

## Conversa
Mensagens são traduzidas em menu → serviços múltiplos → profissional →
data → hora → resumo → confirmação. Redis tem TTL de 30 minutos; chaves
incluem tenant, canal e remetente. O bot chama o motor de agendamentos da
FATIA 2 para reservar/confirmar. Falhas de disponibilidade solicitam recomeço.
Mensagem "atendente" suspende o menu por 30 minutos. Sem configuração
`bot_enabled` no canal e no tenant, nenhuma resposta nova é gerada.

O provedor Cloud API suporta envio de texto com acesso autorizado. Listas
e botões têm fallback de texto. Templates são bloqueados até que o canal
seja aprovado para seu uso — nunca emitir campanhas ou notificações fora
da janela de atendimento por conta própria.

## Ambiente seguro
`META_APP_SECRET` e `META_VERIFY_TOKEN` nunca vão para Git, payload ou
logs. `META_LIVE_SEND=0` (padrão) impede ENVIO REAL e mantém o outbox
pendente; `INTEGRATION_FERNET_KEY` é armazenada somente como segredo de
ambiente. Provisionamento de canais exige confirmação de titularidade e
permissões Meta de maneira supervisionada.

Ainda é necessário: cadastro/autenticação efetiva de operadores via número
Meta com confirmação para comandos sensíveis, gerenciamento de templates,
alertas sobre dead letters, reconciliação de envio pós-crash, provas de
conformidade da janela Meta e testes com conta de sandbox oficial.
Sem isso, a FATIA 3 não deve ir a produção.
