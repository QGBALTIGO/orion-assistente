# Orion — critérios de homologação da conversa inteligente

Fluxo do WhatsApp conectado (Baileys) com consentimento de teste:

- Groq chat: `GROQ_API_KEY` apenas no backend. Para investigar 401/403/429, defina temporariamente `GROQ_PROBE_ON_START=1` no serviço API e leia `probe_result=http_...` no deploy. O sistema não registra credencial, prompts nem respostas privadas nos logs.
- Ativar apenas após validação: no backend `QR_AI_ENABLED=1`, `QR_AI_TENANT_ID` do estabelecimento cadastrado e `QR_AI_ALLOWED_SENDERS` com os telefones completos autorizados. Allowlist vazia **não libera ninguém**. No bridge configurar `QR_AI_ENABLED=1` e ativar chatbot no painel.
- Mensagens de voz: `QR_AI_AUDIO_ENABLED=1` no backend e no bridge, além da opção transcrição no painel, habilita o envio temporário de OGG/Opus até 2 MB à API Groq Whisper Turbo. Nenhum áudio fica armazenado pelo módulo, e a transcrição é feita somente para telefones da allowlist. Também é possível usar transcritor privado faster-whisper se instalado e configurado; não foi encontrado serviço de transcrição implantado neste projeto Railway.
- O motor usa RLS por tenant, consulta preços e horários do banco, recebe nome fornecido voluntariamente, guarda a associação do nome e das reservas no PostgreSQL, e conserva dados contextuais temporários até 7 dias no Redis; isso não é um registro permanente de todas as mensagens.
- Remarcação exige número verificado, reserva confirmada desse cliente, horário real, aceite explícito e revalidação transacional na gravação. O motor não altera a reserva na prévia.
- Mensagens fora de contexto, xingamentos, encaminhamento humano e consultas ao histórico têm respostas seguras e testes internos. O atendimento humano bloqueia auto-respostas por 30 minutos, retornando com MENU.
- Chamadas telefônicas não são recebidas por IA: o controle opcional de bloqueio e resposta usa eventos Baileys, configuração do painel, exceções e janelas de horário. Testes de chamada e entrega com aparelhos reais dependem de intervenção externa e dispositivos consentidos.
- A resposta automática só deve ser avaliada como entregue se houver recibo de entrega; "servidor aceitou" não significa que o telefone conseguiu descriptografar.
- A chave Groq compartilhada em conversa deve ser rotacionada antes de abertura para clientes, mesmo estando em variável de ambiente.

**Plano de testes:** sintaxe Python/Node, CI PostgreSQL/RLS, Docker Compose, smoke do Baileys e ASR, diálogos simulados de múltiplos turnos, teste de voz mockado e, após número válido e opt-in, fluxo real de dispositivo autorizado. Verificar preços, nomes, remarcação, múltiplos clientes e idempotência. Não declarar funcionamento perfeito sem medir entrega real.


## Piloto de destinatário único
Opcionalmente, configurar no *bridge* `QR_AI_PILOT_NUMBER=55DD9XXXXXXXX` (somente número com consentimento explícito).
Quando presente, substitui o toggle geral do chatbot **apenas para esse número**,
mesmo que o toggle global esteja desligado. Para qualquer outro número, o bridge
nem encaminha mensagens à IA. Configurar, adicionalmente, em **ambos** os serviços
`QR_AI_ENABLED=1` e na **API** `QR_AI_ALLOWED_SENDERS=55DD9XXXXXXXX`.
O backend mantém a segunda verificação da allowlist. Para áudio, o piloto precisa
também de `QR_AI_AUDIO_ENABLED=1` nos dois serviços; as demais
configurações de chamada continuam intactas. **Não ativar sem antes verificar que
o número pertence ao participante, o ambiente é demonstrativo e a CI passou.**
O piloto somente responde depois que o participante enviar uma mensagem recente.
