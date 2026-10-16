# Entrega de mensagens com Baileys

O WhatsApp pode exibir "Aguardando mensagem" mesmo quando uma chamada
`sendMessage()` não lança erro. Aceitação não significa descriptografia.

## Mitigações

- `makeCacheableSignalKeyStore` envolve o estado de chaves Signal.
- `getMessage` recupera mensagens do cache transitório por ID, mesmo
  quando o WhatsApp solicitar retry usando LID em vez de PN.
- O cache retém até mil mensagens por 24h na RAM; não expõe conteúdo em logs
  nem endpoints. Reinício perde o cache (limitação atual de homologação).
- `sendMessage` responde `accepted:true,delivery:pending`, não
  anuncia entrega confirmada.
- `messages.update` registra `server_accepted`, `delivered`,
  `read`; o painel usa a confirmação e sinaliza envio pendente.
- O aviso de conexão para a própria conta também entra no cache de
  mensagens para possíveis pedidos de reenvio.
- Não reenvia automaticamente envios ambíguos para evitar duplicatas.

## Critério de aceitação com celular real

O proprietário deve validar um envio manual a um número autorizado,
confirmar que o texto aparece legível no telefone e que os recibos
avançam. Sem isso, não afirmar que o erro de E2EE foi resolvido.

A falha é relatada também em iOS com outras versões do Baileys; essas
mitigações não garantem corrigir todos os casos. Avaliar migração da
versão 6.7.24 para 7 com backup e testes antes de alterar sessões vivas.

Referências:
https://github.com/WhiskeySockets/Baileys/issues/1767
https://github.com/WhiskeySockets/Baileys/issues/1701
https://github.com/WhiskeySockets/Baileys/issues/2297
