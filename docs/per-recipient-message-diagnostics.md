# Diagnóstico sob demanda para mensagens com atraso por destinatário

Relato 2026-10-16: alguns números recebem imediatamente; outros veem
“Aguardando mensagem” por 60 segundos ou mais antes de aparecer o texto.
Hipóteses: session Signal daquele aparelho, presença LID, identidade
do destinatário, sincronização de múltiplos dispositivos e pedidos de retry.
É improvável que um único gargalo HTTP explique destinos rápidos e lentos
na mesma sessão, mas isso não comprova uma causa específica.

- O operador seleciona um número e clica **Verificar destinatário sem
  enviar mensagem**, dentro de Conversas.
- Consulta Baileys `onWhatsApp()` via USync, sem `assertSessions(true)`,
  envio, chave nova ou re-pareamento.
- Exibe registro confirmado, PN correspondente, presença de LID e se
  é o próprio número vinculado; nunca exibe o LID em si.
- Exibe contagem de mensagens enviadas para esse número e de recibos
  pendentes/entregues/lidos coletados pelo bridge. Os recibos do Baileys
  podem faltar; pendente não implica que o texto não tenha carregado.
- Limite de consultas repetidas: até 40 contatos em cache, 90 segundos
  por número. Sem varredura automática de contatos.
- Protegido por login administrativo/cookie+CSRF no painel e token
  somente interno na rede da Railway; nenhum endpoint público aberto.
- O diagnóstico não revela material Signal, número do aparelho
  vinculado fora do painel, IDs internos, corpo das mensagens ou QR.
- A verificação não altera o alvo usado pelos envios existentes e
  não tenta forçar novas sessões criptográficas.

Referências concretas do Baileys:
https://github.com/WhiskeySockets/Baileys/issues/1739
https://github.com/WhiskeySockets/Baileys/issues/1701
https://github.com/WhiskeySockets/Baileys/issues/2297

## Como comparar

Escolha um número rápido e um número lento, ambos autorizados.
Faça a verificação de destinatário antes de qualquer novo envio;
anote somente as classificações (PN, LID, próprio número e quantidade
de recibos), não compartilhe números, IDs nem chaves. Se necessário,
faça um único envio por destinatário, espere pelo menos 2 minutos,
e compare o tempo real de legibilidade no iPhone. Nunca automatize
reenvios ambíguos: podem causar duplicatas.
