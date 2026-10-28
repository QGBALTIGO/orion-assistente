# Cakto / Orion — etapa 1 (infraestrutura)

A integração **não ativa nem cancela assinaturas Orion** e não muda permissões.
Há apenas catálogo de preços e URLs, ingestão assinada e consulta de confirmação
de pedidos pelo servidor. A ligação de um checkout a um usuário será entregue
depois com os cards de planos, sem vincular empresas por e-mail do comprador.

## Endpoints
- GET /api/public/cakto/plans — Essencial R$29,90; Pro R$89,90; Elite R$139,90, com os três links públicos.
- POST /webhooks/cakto — aceita somente HTTPS com HMAC SHA-256 dos cabeçalhos X-Cakto-Timestamp e X-Cakto-Signature e com secret no payload. Formatos V1 e V2. Idempotência via hash do evento do pedido, sem dados pessoais.
- GET /api/v1/admin/cakto/connection — somente suporte/admin; checa OAuth2 de leitura de ofertas e associação exata de nome/preço. Nunca expõe tokens.

## Configuração de produção
Configure no Railway API:
- CAKTO_CLIENT_ID / CAKTO_CLIENT_SECRET — chave OAuth2 de servidor, com read + orders + offers (sem incluir no código).
- CAKTO_WEBHOOK_SECRET — secret **real do webhook configurado** na Cakto, diferente do client_secret. Não presumir que um JSON de exemplo contém o segredo real.

No worker configure CAKTO_CLIENT_ID / CAKTO_CLIENT_SECRET, sem CAKTO_WEBHOOK_SECRET.
O worker checa pedidos via GET /public_api/orders/{id}/; pagamento jamais
é aprovado com base só no webhook. Cancelamento de assinatura exige escopo
read subscriptions, ausente na credencial informada: vai para revisão e não
desativa empresas. Não exportar dados de cartão/cliente nem imprimir segredos.

No painel da Cakto, atualize o webhook de evento para
https://orionagenda.pro/webhooks/cakto, selecionando os produtos Orion e os
eventos purchase_approved, purchase_refused, subscription_renewed,
subscription_canceled, refund e chargeback. A credencial enviada **não tem
escopo webhooks**: não permite editar esse destino pela API. Confirme o
secret real e envie o evento de teste da própria Cakto, verificando 200/2xx.
Os links de checkout NÃO representam IDs de ofertas; esses IDs são obtidos
somente via API de ofertas e devem ser verificados antes do vínculo de planos.

Revisão 0043 cria a tabela restrita cakto_webhook_events (RLS FORCE,
somente app_support); sem customer, CPF, e-mail, cartão ou corpo de evento.
O deploy exige migração única após backup de produção e validação de revisão.
Não executar sem um backup confirmado. A ausência de tabela resulta em
HTTP 503, para que a Cakto faça retry — nunca aceitar evento perdido.

## Próxima etapa
Criar checkout com token único de sessão/empresa, validar vínculo no servidor
(e não por CPF/e-mail/URL aberta), cruzar IDs reais de ofertas/assinaturas, tratar
mudança de plano, duplicidade, prazo de carência, recorrência e cancelamentos
antes de habilitar feature gates e criar cards no painel.
