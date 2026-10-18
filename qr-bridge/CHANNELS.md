# Canais QR adicionais (opt-in)

Este contrato acrescenta contextos independentes ao processo do bridge. Não
importa, move, limpa nem reativa a sessão legada de `/data/auth`. A allowlist e o
piloto do servidor legado continuam intactos. A configuração padrão não cria
nenhum contexto adicional, socket ou diretório.

O backend registra cada canal como `baileys`, vinculado à empresa e a um owner
autenticado e ativo. O cadastro nasce **desabilitado**, tem allowlist explícita
de até dez destinatários de piloto e retorna a credencial somente uma vez.
Repetir a criação retorna o mesmo canal, sem revelar novamente a credencial.
O banco mantém hash e envelope Fernet; `INTEGRATION_FERNET_KEY` é obrigatório
para criar/decriptar, nunca aparece em resposta ou log.

`enabled` permite a sessão; `assistant_enabled` é uma configuração separada,
também falsa por padrão e alterável somente por PATCH explícito do owner com
revisão esperada. Conectar o canal não ativa a IA. O ingresso deve conferir
também `identity.assistant_enabled` e o `tenant.bot_enabled` atual antes de
interpretar/responder; desativação invalida imediatamente o fence anterior.

## Integração do backend

- Incluir `app.qr_channels.router` no aplicativo; `QR_CHANNELS_ENABLED=1` deve
  ser uma decisão explícita do operador. A flag ausente mantém HTTP 423.
- Registro e configuração: `/api/v1/tenants/{tenant_id}/qr-channels`.
- Broker: `POST /internal/qr/channels/{channel_id}/lease/{claim|renew|release|check|inspect}`.
  Credencial em `X-Orion-Channel-Key`; corpo com `owner` UUID e `fence` quando
  aplicável. O banco decide tenant, provider, allowlist e expiração.
- O ingresso `POST /internal/qr/channels/{channel_id}/ai/reply` está integrado
  ao motor em `app.qr_ai.router`. Antes de usar contexto ou fazer uma alteração,
  o guard revalida `authenticated_channel(request, phone=body.phone)`, o bot da
  empresa e a conclusão do onboarding. O registro de IDs/revisões é obrigatório
  nesses canais, independentemente da flag opcional do piloto.
  São obrigatórios os headers `X-Orion-Channel-Key`, `X-Orion-Channel-Owner` e
  `X-Orion-Channel-Fence`. Após trabalho demorado, revalidar imediatamente antes
  da mutação. Usar `identity.context_namespace` em Redis e idempotência.
- Contexto, intake, dedup e recibos são separados por canal UUID. A identidade
  interna de mensagens também inclui o canal para não compartilhar idempotência
  de domínio. Chaves e identidade legadas permanecem exatamente como antes.
- O payload encaminhado contém somente `phone`, `message_id` e `text`.
  Tenant e autoridade nunca são escolhidos pelo texto, modelo ou payload.
- `POST /internal/qr/channels/{channel_id}/interaction` recebe somente
  `{phone,message_id}`, inclusive com IA desligada. Autentica allowlist e lease
  e cancela marketing pendente quando a funcionalidade está habilitada; não
  interpreta texto. Falha nesse aviso bloqueia a resposta. Notice e reply usam
  uma deduplicação compartilhada. O piloto legado cancela após admissão de um
  turno, mas não ganhou aviso quando sua IA está desligada nesta fatia.
- O painel usa proxies autenticados de proprietário em
  `/api/v1/tenants/{tenant_id}/qr-channels/{id}/{status|qr|start|stop}`.
  GETs não iniciam sessão; POSTs exigem `expected_revision`. QR é PNG privado,
  com expiração e `no-store`, nunca a credencial bruta. `disabled` descreve a
  configuração, sem fingir que um socket já terminou de fechar.
- `outbound_channel_credentials(tenant_id, channel_id)` é helper interno do
  servidor, não endpoint. O chamador precisa verificar intenção/consentimento
  e política antes de usar a chave apenas em memória.

## Bridge

`QR_CHANNELS_ENABLED=1` e `QR_CHANNEL_BROKER_URL` apontando à raiz HTTP privada
`*.railway.internal` permitem as rotas novas. Não há descoberta nem conexão
automática. Um `POST /internal/channels/{id}/start` com credencial de canal é
necessário para adquirir lease e instanciar o socket Baileys real.

As rotas `status` e `qr` usam GET; `start`, `stop` e `send` usam POST. As duas
primeiras mutações aceitam `{}`. Envio exige `{to,text,operation_id}`, sendo a
operação UUID. Não existe fallback para o token compartilhado do piloto.
`GET /internal/channels/{id}/operations/{operation_id}` consulta somente o
ledger, inclusive após reinício, sem iniciar socket ou criar diretórios. Uma
operação `unrecorded` tem entrega `unknown`, não autorização de reenvio.

Cada canal tem auth cifrada e ledger em
`/data/orion-channels/<tenant>/<channel>/`. Esse caminho precisa estar em
**volume persistente**. Arquivos POSIX usam 0600 e diretórios 0700; testes no
Windows não comprovam ACLs de produção. O ledger é reservado exclusivamente e
sincronizado em disco antes de enviar. Resultado incerto nunca é reenviado
automaticamente, mesmo após reinício. `accepted` não significa `delivered`;
recibos observados são separados. Nunca se promete entrega externa exatamente
uma vez. Não reutilizar uma operação para conteúdo diferente.

A posse é distribuída via transação PostgreSQL: lease de 30 segundos, renovação,
fence monotônico, abandono por falha/expiração e watchdog local conservador.
Antes de encaminhar ou enviar, validar novamente. Callbacks do socket antigo
não podem escrever ou operar como o novo. Reconexões têm jitter e backoff até
30 segundos, no máximo oito tentativas consecutivas; logout exige ação nova.

## Limites explícitos

O adapter inicial suporta texto privado recente de piloto, QR, envio e recibos.
Áudio, chamadas, perfil/contatos e o retry store completo do fluxo legado ainda
não foram integrados a esses novos contextos. Não há fallback ao singleton.
O host atual continua mantendo a sessão legada existente, além dos contextos
adicionais abertos explicitamente. A interface de seleção tem contrato público
independente; esta documentação não comprova teste físico de pareamento.

Limites iniciais: dez contextos locais por manager, cinco admissões de envio
por minuto por contexto e 32 processamentos de entrada pendentes por contexto.
São limites de proteção, **não evidência de capacidade** para mil sessões.
API, migração, ingressos e transporte precisam de testes PostgreSQL/integração
e validação autorizada antes de qualquer ativação. Nenhuma ativação de produção
faz parte da criação destes arquivos.
