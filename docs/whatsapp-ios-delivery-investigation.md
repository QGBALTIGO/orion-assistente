# Investigação de atrasos E2EE no WhatsApp iOS

Observado pelo proprietário: placeholder 'Aguardando mensagem' antes do texto
ficar legível no iPhone. Isto indica recuperação tardia, não uma causa única.

Fontes consultadas:
- Ajuda oficial: https://faq.whatsapp.com/3398056720476987/
- Baileys: issues #1767, #1701, #2297 e #1964
- Evolution API: issue #2705 (getMessage nunca deve devolver texto vazio)
- Baileys PR #2711 (PN→LID e mudanças de identidade), aberta em 2026-10.
- Implementação comparativa: rezadaulay/expressjs-baileys no GitHub.

Mitigações do Orion (QR_RELIABLE_SEND_V2=1):
1. Gerar uma única mensagem via generateWAMessage e salvar o proto antes
   de relayMessage, preservando o mesmo ID em todos os retries.
2. getMessage consulta cache em memória e cópia criptografada no volume
   privado com HKDF + AES-256-GCM, TTL 48h e no máximo 500 registros.
3. Nunca registrar textos, telefones ou IDs em logs. Arquivos 0600 e
   identificadores de arquivo por HMAC; sem endpoint HTTP de exportação.
4. Não alterar/limpar chaves Signal nem pedir re-pareamento.
5. Flag QR_FORCE_FRESH_DEVICES=1 permite atualização da lista de aparelhos
   para evitar destinos obsoletos, com eventual custo de latência.
6. Exibir aceite e recibos de entrega separadamente, sem alegar entrega
   quando o send/relay apenas terminou.
7. Nunca reenviar automaticamente mensagens com resultado ambíguo.

Limite: o protocolo não oficial não garante descriptografia instantânea
em todos os aparelhos iOS. Baileys 7 rc14 também tem relatos semelhantes;
upgrade sem backup e validação pode perder sessão. Essa versão do Orion
permite desligar o caminho novo com QR_RELIABLE_SEND_V2=0.

Aceitação: teste manual com apenas uma mensagem a uma conta consentida,
medir o atraso observado e recibos; se piorar, reverter a flag.
Antes de uso real em SaaS, revisar privacidade, retenção e LGPD.
