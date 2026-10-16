# Sessão persistente e confirmação de vínculo

O serviço `orion-qr-bridge` usa **volume Railway em /data**, pasta ativa
em `/data/auth` ou `/data/auth-renew-...`, carregada novamente no boot.
Após `creds.update`, os dados de autenticação são gravados sequencialmente
antes da retomada por status 515. Credenciais e chaves do Signal devem ser
preservadas conjuntamente. Evite limpar o volume e evite várias réplicas
simultâneas do mesmo dispositivo.

- Desconexões temporárias (ex.: 408) são reconectadas com atraso progressivo.
- `restartRequired` (515) é reconexão rápida e normal após pareamento.
- `loggedOut` (401) é revogação: **nunca** contornar ou reautenticar sem
  confirmação do proprietário; exigir novo QR/código.
- O painel deve mostrar o status reportado pelo socket, não o health check
  HTTP como prova de conexão.

## Mensagem de confirmação

Ativar somente por `ORION_NOTIFY_SELF_ON_LINK=1` no bridge. Em evento
`connection.update: open`, aguardar 2,5 segundos e conclusão de saves.
Validar que o socket permanece atual e o destino vem exclusivamente de
`sock.user.id` autenticado; enviar texto fixo:

> 🎉 Dispositivo Conectado com Sucesso
>
> O Orion foi conectado ao seu WhatsApp! ✅

Recibo `orion_link_notification.v1.json` é criado no diretório da
autenticação no volume, com permissão 0600 e sem dados pessoais. Isso
bloqueia novos disparos após restart ou reconexões simultâneas. Ao
autorizar vínculo novo em uma nova pasta de credenciais, a confirmação
pode ser enviada novamente. Falhas de envio com resultado ambíguo
**não são reenviadas automaticamente**, para evitar mensagens duplicadas.
O próprio destinatário verá o texto em “Mensagens para mim” se o
servidor WhatsApp aceitar a entrega.

Os testes sintéticos verificam a deduplicação, persistência do recibo,
destino da própria conta, falha ambígua e política de reconexão.
Eles não substituem uma prova de envio real após o pareamento no celular.

## Limites

Baileys é **não oficial**; pode haver revogação, mudança no protocolo
ou restrições pela plataforma. Nem o Orion nem o BotBot podem garantir
conexão literalmente infinita. O `useMultiFileAuthState` é adequado
somente à homologação atual e não é recomendado pelos autores do
Baileys para um SaaS em produção; é necessário auth store robusto,
backup, criptografia e revisão de segurança antes de clientes reais.
