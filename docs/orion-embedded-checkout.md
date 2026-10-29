# Checkout Orion integrado — Pix / cartão / conciliação

Esta implementação é a nova interface de assinatura do Orion. Ao selecionar Assinar, o cliente
continua no painel do Orion e vê um diálogo nativo com o preço e o plano escolhidos.
O frontend não contém links para o checkout hospedado.

## Segurança e habilitação

**Fail closed**: `ORION_DIRECT_PAYMENTS_ENABLED=0` e `ORION_DIRECT_RECURRING_VERIFIED=0` por padrão. A flag legada
`CAKTO_BILLING_LIVE_ENABLED`, o segredo de webhook HMAC e o OAuth ativo também
são obrigatórios. Até a homologação real, a tela mostra "Pagamento integrado
temporariamente indisponível" e não envia `POST /payments/`.

Para habilitar é obrigatório comprovar:
1. Credencial OAuth com escopos `payments:write`, leitura de pedidos e ofertas,
   tokenização de cartão e 3DS; estes escopos NÃO estão comprovados pela
   credencial atual de leitura.
2. Cakto Banking ativa, principal e regularizada para cobranças Pix.
3. Teste de webhook **assinado**, reentregas, consulta `GET order` e correlação
   única de `sck`, preço, oferta, assinatura, conta e empresa.
4. Confirmação formal de que a cobrança direta de uma oferta de **assinatura**
   cria uma assinatura recorrente de fato, também para Pix (Pix comum é
   pagamento pontual, Pix Automático é outro fluxo). Não ativar cobrança
   recorrente sem um contrato de renovação/cancelamento verificável.
5. Sandbox de pagamentos do provedor, incluindo tokenização e desafio do banco,
   aprovado nos casos sucesso, recusa, timeout e tentativas duplicadas.
6. Conformidade PCI DSS/SAQ adequada à integração do cartão e textos
   obrigatórios de operador e privacidade. Não falsificar informações em
   comprovantes, política de privacidade ou autenticação bancária.

A disponibilidade também executa uma **checagem somente de leitura** em
`GET /public_api/webhook/`. Ela exige um webhook ativo com destino exato
`https://orionagenda.pro/webhooks/cakto`, segredo coincidente e os eventos
necessários para os três produtos vinculados às ofertas mensais. Sem cobertura
3/3 ou se a leitura falhar, a cobrança direta é recusada, mesmo com as flags
ativadas. `scripts.probe_cakto_read_only` imprime somente o número de planos
com a cobertura verificada, sem segredos nem dados pessoais. Isso não substitui
uma **entrega real de evento de teste assinado**, que ainda é necessária para
homologar a entrada em produção.

**Webhook V2 criado no painel:** a listagem `GET /public_api/webhook/`
pode não conter esse cadastro. Quando não houver 3/3 no inventário V1, o
Orion consulta o histórico oficial de entregas
`GET /public_api/webhook/event_history/` (somente leitura). A alternativa
V2 só passa após encontrar **todos os seis eventos financeiros necessários**
com HTTP 2xx, segredo correspondente, destino HTTPS exato, produto associado
às três ofertas e prova de entrega nos últimos 90 dias. O histórico pode
conter CPF, e-mail e dados de venda: nenhum corpo, token ou identificador
é registrado em logs ou armazenado pelo diagnóstico.

As flags `ORION_DIRECT_PAYMENTS_ENABLED` e
`ORION_DIRECT_RECURRING_VERIFIED` permanecem **desativadas** durante a
homologação. O histórico V2 validado não ativa cobranças por si só.
É obrigatório substituir qualquer chave de webhook exposta antes de receber
pagamentos reais de clientes e refazer o teste da entrega assinada.

A UI de produção usa identidade Orion. Os dados do cartão são tokenizados
**no navegador pelo SDK**; o servidor Orion nunca recebe PAN/CVV.
Revisar eventual necessidade de campos hospedados para reduzir escopo PCI.

## Fluxos e endpoints

- `GET .../plan-direct-config`: disponibilidade para o proprietário da empresa;
  flag falsa devolve apenas `available:false`.
- `POST .../plan-direct-prepare`: reutiliza a intenção existente, valida o
  proprietário, oferta mensal exata, valor e estado da assinatura sem cobrar.
- `POST .../plan-direct-charge`: reserva tentativa única antes do envio, valida
  nome, telefone e CPF/CNPJ e usa uma chave de idempotência por tentativa. Um
  timeout deixa a tentativa em `unknown` e bloqueia repetição automática.
- `GET .../plan-direct-status`: só o dono pode consultar o status;
  o retorno não é usado para conceder acesso em cliente.
- `/webhooks/cakto` e `GET /public_api/orders/{id}/`: continuam sendo as
  únicas autoridades para ativar uma assinatura. Um pagamento direto precisa
  de uma reserva que corresponda exatamente ao mesmo pedido e ofertar uma
  assinatura válida; um pedido avulso não libera plano.
- QR Pix gerado localmente com `qrcode` no servidor. Sem serviço externo de QR.
- Assinaturas mensais por Pix usam `pix_auto` (Pix Automático), nunca `pix` pontual. O cliente autoriza débitos futuros no banco, e um evento `purchase_approved` ainda precisa de reconciliação antes de ativar o acesso.

## Migração e lançamento

`0046_orion_direct_checkout` cria `orion_direct_payment_attempts` com RLS FORCE,
`app_support` somente, sem CPF, email, PAN, CVV ou payload do PSP.
O pré-deploy exige o backup privado verificado da revisão `0045` antes de avançar.
O worker aguarda esquema anterior e não envia cobranças em background.

Checagens na PR: pytest unidade (incluindo antifraude, validação e preço),
PostgreSQL real com migração `0046`, DOM sintético e Chromium responsivo.
Não executar cobranças reais em CI. O segredo HMAC jamais deve ser exportado
para o cliente; apenas `client_id` público do SDK.

Links oficiais para homologação:
- https://docs.cakto.com.br/sdk/visao-geral
- https://docs.cakto.com.br/sdk/tokenizacao
- https://docs.cakto.com.br/sdk/3ds
- https://docs.cakto.com.br/api-reference/payments/create-pix
- https://docs.cakto.com.br/api-reference/payments/create-3ds
