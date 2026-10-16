# Conexão WhatsApp por QR — experimento opt-in

**Situação:** método não oficial e separado do canal Meta Cloud API.
A integração Baileys é para **um número de teste autorizado** e não
se mistura ao motor de reservas. O bridge **não** tem manipulador de
mensagens recebidas, envio de mensagens, webhook, ou endpoint de envio.

## Arquitetura
- Serviço `orion-qr-bridge`: Node 22, biblioteca Baileys 6.7.24,
  volume persistente Railway em `/data`, dados em `/data/auth`,
  sem domínio público; `/internal/status`, `/internal/qr` e
  `/internal/reconnect` requerem cabeçalho secreto.
- Serviço `orion-assistente`: proxy `/api/v1/qr/` com token
  de operador de suporte; página estática `/conectar-whatsapp`.
  `QR_BRIDGE_URL=http://orion-qr-bridge.railway.internal:3000`.
- O QR fica só em RAM e expira em cerca de 45 segundos.
  Nenhuma chave de sessão vai ao navegador. `QR_BRIDGE_TOKEN`
  precisa ser gerado aleatoriamente com mínimo 32 caracteres
  e configurado nos dois serviços de modo privado.
- Persistência: volume Railway; backups da sessão precisam de
  política específica antes de uso com clientes reais.
- Nunca adicionar domínio público ao bridge.
- Usar apenas número sob controle e consentimento explícito.
  É integração não oficial, sujeita a desconexões, bloqueios e
  riscos de segurança e privacidade.

## Como testar
1. Acesse `https://orion-assistente-production.up.railway.app/conectar-whatsapp`.
2. Informe o `SUPPORT_API_TOKEN` obtido nas variáveis privadas da
   Railway; nunca cole senha/token em chats ou URLs.
3. Ao surgir QR, escaneie com WhatsApp/WhatsApp Business:
   Dispositivos conectados → Conectar dispositivo.
4. Aguarde o status **Conectado**.
5. Encerre a sessão no próprio WhatsApp → Dispositivos conectados
   quando terminar o teste.

**Não é a conexão oficial Meta Cloud API, não inicia bot, não faz
agendamentos.** Exigir validação jurídica/técnica antes de conectar
número comercial. O número proprietário é quem decide se quer escanear.

Referências: https://github.com/WhiskeySockets/Baileys e
https://faq.whatsapp.com/378279804439436/ .
