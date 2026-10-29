# ORIONTESTE: botão de link no WhatsApp

## Limitação confirmada
O **Baileys 6.7.24 (WhatsApp por QR)** não oferece um botão de URL nativo
com renderização confiável. O formato experimental `templateButtons` foi
removido da rota de envio. Não confundir ACK/ID de mensagem com botão
efetivamente exibido. O fallback por QR sempre mantém a URL visível no texto.

## Teste autorizado
- Gatilho: mensagem privada recebida `ORIONTESTE` (correspondência exata).
- Destinatário aceito: telefone já configurado em `QR_TRIGGER_TEST_PHONE`.
- Mensagem: confirmação **fictícia**, sem criar qualquer agendamento.
- `QR_TRIGGER_CTA_PROVIDER` ausente ou `qr_link`: envia texto + link HTTPS
  pelo canal QR atual. Não promete botão.
- `QR_TRIGGER_CTA_PROVIDER=meta` **e** credenciais oficiais configuradas:
  envia mensagem interativa `cta_url` pela **Cloud API da Meta**.
  Não usa comandos Baileys experimentais. Nenhum envio em massa.
- Se a opção Meta for solicitada sem credenciais, envia somente o fallback
  por QR e registra `trigger_test_cta_not_configured`.
- Se a Cloud API for chamada e falhar/expirar, **não** repete via QR:
  evitar duplicidade quando o resultado do servidor for ambíguo.

## Ativação Meta (somente após onboarding e confirmação do remetente)
Provisionar como variáveis seguras **somente no serviço QR Bridge**:
- `QR_TRIGGER_CTA_PROVIDER=meta`
- `META_CLOUD_PHONE_NUMBER_ID`: ID do número registrado na Cloud API
- `META_CLOUD_ACCESS_TOKEN`: token autorizado a enviar mensagens
- `META_CLOUD_API_VERSION`: opcional, padrão `v25.0`

**Não** presumir que o WhatsApp conectado por QR já seja uma conta Cloud API.
O `phone_number_id` deve corresponder ao remetente desejado. Para manter
o aplicativo WhatsApp Business com o mesmo número, verificar elegibilidade e
habilitar coexistência no onboarding oficial antes de ativar. Isso pode
desvincular dispositivos Web existentes: fazer em janela planejada com acesso
ao celular titular. Não mudar sessão QR nem inserir token por chat.

## Validação e auditoria
1. `node --test qr-bridge/test/test-url-button.test.mjs` valida payload,
   allowlist, URL fallback, recusa e resposta da API simulada.
2. `node --check qr-bridge/src/server.mjs` valida o código Node.
3. Em homologação com Cloud API configurada, enviar o gatilho para o telefone
   autorizado, confirmar status Meta e **inspecionar o botão no app de destino**.
4. No Railway, interpretar `trigger_test_cta_api_accepted` como aceite
   do servidor, não como entrega ou exibição. Comprovar em webhook de status
   e verificação do destinatário antes de declarar aprovado.
5. Sem Meta Cloud ativa, um resultado com link clicável é comportamento
   esperado, não um botão nativo.

Esta rota serve apenas ao piloto. Não torna todas as confirmações do Orion
mensagens interativas, e não deve ser generalizada aos clientes sem desenho
de canal, consentimento e processamento de recibos.
