# Central WhatsApp conectado — estado com sessão existente

## Interface

Quando `/api/v1/qr/status` retorna `connected`, a aba **Conectar dispositivo**
oculta as instruções e QR e mostra a central de controle com foto, nome, número,
métricas, opções, diagnósticos e configurações. Não replica assets do botbot.chat.
Se o WhatsApp não informar nome ou foto, exibe fallback honesto.

O perfil é solicitado pelo método Baileys `profilePictureUrl`, sem URL inventada
e com allowlist dos domínios CDN do WhatsApp. A identidade é obtida da
sessão vinculada, não digitada no painel.

## Contagens auditáveis

O arquivo criptografado `/data/auth/connected-control.enc` é restrito ao
usuário do processo. Somente mensagens **novas recebidas e enviadas que
entraram na caixa do Orion** são contadas. Janelas hoje/7/30 dias seguem o fuso
America/Campo_Grande. Os contadores continuam após reinício e
deduplicam eventos recentes, com limite de 3000 hashes sem PII. Não alegar
que o total abrange toda a conta WhatsApp ou histórico anterior: o painel
mostra expressamente 'desde o início da medição'. O fluxo deve ser validado
em produção com mensagem consentida antes de prometer cobertura completa.

## Controles persistentes

A API `POST /api/v1/qr/settings` só aceita sessão autenticada de suporte e
um esquema fechado. Estados iniciais são **desligados**; o perfil e os valores
persistem criptografados no volume, associados ao identificador da conta.

- **Chatbot:** aparece indisponível até uma integração real entre a instância
  vinculada e o tenant da agenda. Não inventa uma resposta automática.
- **Transcrição:** opcional e desativada até configurar
  `QR_ASR_URL=http://<serviço>.railway.internal:8000/transcribe` e
  `QR_ASR_TOKEN` longo. Implementação OSS em `qr-transcriber/`
  com faster-whisper CPU. Processo separado, privado, sem deploy por padrão.
  O conector só processa áudios novos até 2 MB e armazena texto na caixa
  criptografada. Não manda texto para terceiros nem ao remetente. Conteúdo
  sensível exige aviso de privacidade e consentimento conforme o contexto.
- **Chamadas:** apenas eventos `offer` individuais com PN validado são
  avaliados. Se ativado, rejeita via método `rejectCall` do Baileys.
  Pode permitir números e horários no fuso de MS. A mensagem de resposta é
  separada e desativada por padrão, só é enviada se expressamente selecionada.
  No máximo uma resposta por número na sessão, além da limitação já existente
  de envios. Chamadas @lid sem PN verificado não são rejeitadas às cegas.
  Baileys é não oficial; rejeição pode não funcionar em todas as versões.

## Reiniciar e desconectar

**Reiniciar:** exige confirmação, bloqueia repetições dentro de 60 segundos,
não apaga chaves e reinicia somente o socket. **Desconectar/trocar:** exige
digitação literal `DESCONECTAR` e segunda confirmação. Executa
`logout()` e deixa as credenciais antigas sem exclusão automática.
A reconexão com outra conta usa fluxo existente de recuperação após revogação.
Nenhuma dessas ações foi executada nesta implementação.

## Segurança e rollout

Esta branch parte da correção de proteção dos logs Signal (PR #11), para
evitar imprimir objetos com chaves de sessão em logs futuros. Logs passados
requerem revisão de retenção/acesso. PR #10 e PR #12 permanecem independentes.
Não atualizar a main nem publicar na Railway antes da CI, da revisão e do
planejamento de implantação. Não declarar transcrição/chatbot ativos se
não estiverem configurados. Não transcrever áudio de contatos sem as
bases legais e informações de tratamento aplicáveis.

## Aceitação manual após deploy autorizado

1. Verificar foto/nome reais, ou fallback honesto, em dispositivo já conectado.
2. Verificar contadores iniciando do zero e avançando com mensagens
   consentidas, sem duplicidade.
3. Alterar regra de chamada e confirmar persistência após restart controlado.
4. Testar chamada autorizada e bloqueada em números de teste consentidos.
5. Transcrever áudio de teste apenas depois de configurar backend privado.
6. Confirmar que o botão de desconexão pede duas confirmações;
   **não executá-lo na sessão de produção durante testes visuais**.
7. Verificar controle de acesso/CSRF, RLS onde aplicável, ausência de segredos
   nos logs e fallback quando serviços opcionais estiverem indisponíveis.
