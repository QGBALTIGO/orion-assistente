# Orion / Cakto — Revisão em produção (2/11/2026)

Este documento não contém segredos, dados de clientes ou ordens reais.

## Estado confirmado pela API e pelo deploy

- OAuth Cakto: autenticação e consulta de ofertas funcionando.
- Três ofertas mensais reconhecidas: Orion Essencial R$ 29,90; Profissional R$ 89,90; Elite R$ 139,90.
- Um produto comum às três ofertas, com Pix Automático e cartão detectados.
- Existe um webhook **V2**, cadastrado no painel do provedor, ativo e associado a esse produto, com destino exato `https://orionagenda.pro/webhooks/cakto`.
- O endpoint V1 `GET /webhook/` não lista o webhook V2. O histórico `GET /webhook/event_history/` contém as entregas e dados de configuração permitidos pela API.
- A chave do webhook V2 foi sincronizada com `CAKTO_WEBHOOK_SECRET` na Railway, sem disponibilizá-la em commits ou logs. O código de transferência temporário foi removido.
- Após a sincronização: 26 entregas históricas HTTP 2xx com segredo validado, evidência dos seis eventos financeiros obrigatórios e cobertura de todas as três ofertas.
- Teste da API do provedor `POST /webhook/event_test/{id}/?event=checkout_abandonment`: **entrega HTTP 2xx confirmada** pelo histórico; nenhum pagamento ou assinatura criado.
- Flag transitória `ORION_CAKTO_SYNTHETIC_WEBHOOK_TEST` restaurada para `0`.

## Segurança e próximos requisitos comerciais

- O recebimento das notificações está funcionando. Não criar um webhook V1 duplicado para o mesmo produto.
- A sincronização não é uma rotação de chave. Antes de liberar pagamentos reais, avaliar a necessidade de rotação no próprio provedor, sobretudo se a chave anterior teve exposição.
- Checkout direto com Pix Automático/cartão continua desativado por precaução. Não habilitar `ORION_DIRECT_PAYMENTS_ENABLED`, `CAKTO_BILLING_LIVE_ENABLED` ou `ORION_DIRECT_RECURRING_VERIFIED` sem homologar o ciclo financeiro recorrente.
- Falta confirmar, com staging autorizado e evidências adequadas, cobrança recorrente Pix Auto, tokenização e cartão 3DS, primeira cobrança e ativação, renovação, cancelamento, inadimplência, estorno/chargeback, deduplicação e isolamento entre empresas.
- O worker reconciliador deve ser ativado apenas após revisar os vínculos e testes de ciclo de vida; não fazer mudanças em assinaturas a partir de dados históricos não conciliados.
- Nunca declarar uma assinatura ativa com base somente no webhook: verificar pedido e assinatura no provedor e vincular ao checkout criado pelo titular do negócio.
- Proibido armazenar PAN/CVV, expor chaves em logs ou alterar planos de outras empresas.

Fontes: https://docs.cakto.com.br/conceitos/webhooks e https://docs.cakto.com.br/api-reference/webhooks/test-webhook.
