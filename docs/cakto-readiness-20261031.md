# Orion / Cakto — Estado de homologação (31/10/2026)

Este documento é um registro técnico; não autoriza cobranças e não contém credenciais.

## Evidências em produção
- OAuth de leitura Cakto: respondendo.
- Ofertas mensais ativas identificadas: Essencial R$ 29,90; Profissional R$ 89,90; Elite R$ 139,90.
- Métodos no produto compartilhado: Pix Automático e cartão detectados.
- Webhook para https://orionagenda.pro/webhooks/cakto com cobertura confirmada: 0/3 ofertas.
- Histórico de entregas verificadas com o segredo Orion: 0.
- CAKTO_WEBHOOK_SECRET: não passou a validação de segurança mínima; não registrar nem imprimir seu valor.
- Checkout direto permanece bloqueado intencionalmente. Não mudar flags para contornar a checagem.

## Configuração necessária no próprio provedor
1. Entrar na Cakto da conta que possui as três ofertas de assinatura.
2. Em Integrações > Webhooks, cadastrar um webhook para o produto dono das três ofertas, com a URL EXATA https://orionagenda.pro/webhooks/cakto.
3. Assinar purchase_approved, purchase_refused, subscription_renewed, subscription_canceled, refund e chargeback. Confirmar que o escopo inclui todos os planos do produto.
4. Gerar/rotacionar o segredo no provedor. Configurar o mesmo segredo como variável protegida CAKTO_WEBHOOK_SECRET na Railway, sem expor em chats, prints, logs ou repositórios.
5. Fazer uma entrega de teste pelo provedor; verificar assinatura HMAC, timestamp, segredo do corpo e resposta HTTP 2xx. Consultar o histórico e a reconciliação sem alterar assinaturas reais.
6. Homologar assinatura mensal com Pix Automático e cartão/3DS, incluindo cobrança, recusa, vencimento, renovação, cancelamento, reembolso e chargeback. Validar um único checkout por empresa.
7. Somente após provas documentadas, configurar as flags de billing/reconciliação necessárias e executar novamente as verificações de ofertas, webhooks, métodos recorrentes, assinatura e isolamento.

## Salvaguardas
- Não criar cobrança real nem habilitar pagamentos enquanto o provedor não estiver integralmente validado.
- Webhook não é prova de assinatura ativa: GET de pedido/assinatura e vínculo inequívoco ao proprietário continuam obrigatórios.
- O botão de assinatura pode exibir estado informativo durante a homologação; nenhuma resposta de UI libera permissões.
- Manual grants, testes grátis e assinaturas financeiras são fontes distintas. Não substituir assinaturas existentes por engano.
- Não inferir renovação a partir de datas locais nem ativar planos com recibos de uma compra isolada.
- Preservar idempotência, RLS, limites de profissionais, migrações já instaladas e snapshots dos pagamentos.

Fontes Cakto: https://docs.cakto.com.br/conceitos/webhooks e https://docs.cakto.com.br/api-reference/webhooks/create.
