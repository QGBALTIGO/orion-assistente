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


## Estado sem dispositivo: assistente guiado

O painel mostra **todos os passos do tutorial de uma vez**, mantendo apenas uma etapa funcional (tutorial, QR, código ou sucesso) visível:

1. O modo Código é selecionado por padrão, com opção de alternar para QR Code sem disparar nenhuma solicitação ao WhatsApp.
2. Linha do tempo única com sete passos, SVGs, e **um único botão no fim:** “Concluí os passos e estou pronto para prosseguir”. Sem paginação.
3. Mostrar QR a partir de `/api/v1/qr/image` (validade fornecida pelo servidor,
   contagem regressiva local e ação manual de atualizar), **ou** pedir código via
   `/api/v1/qr/pair` (telefone completo, copiar código, expiração verdadeira).
4. Confirmar o sucesso **somente** a partir de
   `/api/v1/qr/status` com `status:connected`, animar SVG e abrir a central.

Os controles usam os endpoints antigos, autenticação de suporte e CSRF já
implementados. Nenhum QR/código é inventado, nenhum número é transmitido
antes de o operador clicar para continuar, e um QR expirado não dispara
reconexões contínuas sem ação manual.

### Orientação visual

O arquivo fornecido com o nome `scan-demo.gif` não era um GIF animado:
seus bytes correspondem a PNG estático com fundo em gradiente, sem quadro
instrucional de conexão. Para não mostrar um tutorial falso ou quebrado,
o painel inclui uma **animação SVG/CSS nativa** de celular, leitura do QR e
toque, sem links externos ou assets proprietários. A renderização respeita
`prefers-reduced-motion`. Se posteriormente for fornecido um GIF animado
válido, pode substituir a ilustração preservando a etapa de tutorial.

### Cobertura de testes

`qr-bridge/test/wizard-journey.test.mjs` executa a lógica do
`panel.html` em ambiente sintético: mudança de modo, sete passos visíveis juntos, um botão final,
solicitação de QR na hora certa, validade real e expiração, telefone
validado, resposta real simulada de pareamento, cópia, retorno seguro ao
formulário e animação de sucesso somente após status conectado.
`tests/unit/test_panel_session.py` verifica o painel autenticado e
os endpoints, e o pipeline executa build da imagem real Baileys,
PostgreSQL, Docker Compose e saúde HTTP.

**Não há como provar emparelhamento WhatsApp real nem entrega no iPhone
somente com testes simulados.** Após revisão, testar em sessão de homologação
antes de trocar a tela em produção. Não revogar ou resetar automaticamente
a sessão que já está vinculada.

### Ajuste visual (16/10/2026)

Foram removidos da interface pública os textos “Conexão não oficial,
limitada ao número que você administra.” e “Orion · Ambiente de homologação ·
Conecte somente números e dados autorizados.” A recuperação de vínculo
continua acessível quando o backend reporta sessão revogada. Nenhuma das
alterações exige reiniciar ou alterar a autenticação do conector.
