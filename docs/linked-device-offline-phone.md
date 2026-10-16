# Orion — WhatsApp multidispositivos sem depender do celular online

## Arquitetura existente, auditada em 2026-10-16

- A API e o painel são clientes HTTP; não são proprietários do socket WhatsApp.
- O bridge Node com Baileys estabelece a sessão WebSocket diretamente entre Railway e WhatsApp.
- A Railway mantém uma réplica e um volume persistente privado em /data.
- A autenticação do WhatsApp está em /data/auth ou em diretório de renovação,
  incluindo chaves Signal. O bridge lê as credenciais no startup e só pede
  QR quando não existe sessão válida ou o WhatsApp realmente a revoga.
- Reconexões temporárias usam backoff. Erro 515 retoma rapidamente;
  erro 401 nunca é contornado automaticamente.
- Não há nenhum envio/resposta automático a mensagens recebidas neste estágio.

## Melhorias desta fatia

1. Estado `ingestion` separado de `status=connected`: `initializing`,
   `receiving`, `awaiting_initial_sync` e `disconnected`. O estado connected
   NÃO comprova que o stream de eventos de mensagens foi liberado.
2. Ao processar mensagens recebidas, aceita `notify` e também `append` recentes
   (até 15 minutos) para capturar pequena fila de reconexão, sem importar
   o histórico antigo, sem grupos ou mídia e sem auto-resposta.
3. Persiste mensagens privadas de texto recentes em um inbox cifrado com
   AES-256-GCM + HKDF, em volume /data, arquivo 0600 e gravação atômica.
   Retenção de no máximo 500 registros e sete dias, incluindo estado dos recibos.
   Restaurar o processo ou fechar o painel não perde os textos já recebidos.
4. Se a caixa persistida estiver ilegível/corrompida, não a sobrescreve:
   o status reporta `inboxPersistence=unreadable` e mantém memória corrente.
5. O painel informa que o celular principal não precisa ficar online após
   vincular e distingue socket conectado de recebimento comprovado.
6. Não altera a sessão atual, não envia mensagens para testes automáticos,
   não solicita permissões de telefone nem dispara regras ou comandos.

## Limites documentados

- Segundo a Central de Ajuda do WhatsApp, dispositivos conectados funcionam
  sem telefone online, mas o dispositivo principal ainda deve ser usado
  periodicamente (14 dias).
  https://faq.whatsapp.com/378279804439436/
- Baileys é NÃO OFICIAL e a plataforma pode restringir/desconectar.
- Baileys v6.7.24 apresenta issue sobre `initial event buffer` preso durante
  a sincronização: https://github.com/WhiskeySockets/Baileys/issues/2810
  Não forçamos flush interno do buffer para não arriscar corrupção/perda.
- O atraso `Aguardando mensagem` no iPhone é fenômeno E2EE distinto de
  precisar da internet no celular principal. Não há garantia de zero atraso.
- O `useMultiFileAuthState` é utilitário de protótipo; um SaaS multitenant
  precisa de credenciais criptografadas, transacionais e por número/tenant.

## Teste real obrigatório com celular offline

1. Abrir Painel > Conectar dispositivo e conferir `connected`.
2. Verificar `ingestion` em 'receiving' quando houver eventos, sem confundir
   'awaiting_initial_sync' com prova de falha.
3. Colocar o celular principal em modo avião / desligar Wi-Fi e dados.
4. Usar OUTRO telefone autorizado para enviar uma mensagem de texto única.
5. Abrir Painel > Conversas em outro aparelho com internet: mensagem nova
   precisa aparecer sem reativar o telefone principal.
6. Responder manualmente pelo painel e confirmar entrega no outro telefone.
7. Reiniciar apenas o bridge, reabrir o painel e conferir inbox persistente
   sem novo QR. Não usar recibo de `sendMessage` como prova de entrega.

Não ativar auto-replies antes de haver cadastro de comandos, escopo do
destinatário, limites de tráfego e autorização explícita.
