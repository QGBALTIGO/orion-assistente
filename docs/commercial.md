# FATIA 6 — Comercial (prévia de implementação, não ativado)

Landing original em / com visual grafite/dourado, exemplo de conversa
explicitamente fictícia, conteúdo em português, acessibilidade básica e
CTA WhatsApp somente quando SALES_WHATSAPP_E164 for configurado com número
válido. Sem preço fixo divulgado, sem depoimentos ou clientes inventados.

Assinatura SaaS é separada de pagamentos por serviços da barbearia.
Tabelas `plans`, `subscriptions`, `billing_events` e
`usage_counters` permitem cinco estados: trialing, active, past_due,
suspended, canceled. Suspensão impede o bot, mas não exclui agenda/dados.
Planos não são precificados automaticamente, nem criados com dados reais.

Asaas: /webhooks/asaas valida header `asaas-access-token` comparando
constant-time com ASAAS_WEBHOOK_TOKEN, mínimo 32 caracteres. Usa unique
provider_event_id para reentregas. O tenant é resolvido pelo vínculo
prévio de assinatura no banco; um evento recebido NUNCA ativa sozinho
o plano. `reconcile_event` consulta o pagamento no provedor sandbox e
só altera estado quando ID e assinatura estão coerentes. O endpoint
público sempre responde com mínimo de informação.

Documentação oficial: https://docs.asaas.com/docs/sobre-os-webhooks
A API de consultas usa SOMENTE `https://api-sandbox.asaas.com/v3`;
nenhuma chamada de criação de cobranças foi construída ou executada.

**Faltam para comercialização:** onboarding contratual, termos LGPD,
métodos de pagamento reais, confirmação junto a conta autorizada,
reconciliação agendada e testada com Asaas sandbox, política de tolerância
e limites efetivamente testada com múltiplas barbearias.

A integração está bloqueada por falta de credenciais reais. Não habilitar
uso comercial enquanto FATIA 5 não tiver aceite de piloto real.
