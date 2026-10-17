# Groq no Orion — Etapa inicial: intenção e simulação segura

A integração utiliza a API de Chat Completions da Groq com JSON Object Mode.
**Estado: preview sem envio, sem alteração de reservas, sem integração automática
com o WhatsApp e sem conexão Google Calendar disparada pela IA.**

## Configuração

Definir como segredo no backend `GROQ_API_KEY` (nunca no frontend e nunca em Git).
O modelo padrão é `openai/gpt-oss-120b`, ajustável por `GROQ_MODEL` para um dos
modelos permitidos em `app/groq_ai.py`. `GROQ_PREVIEW_ENABLED=0` mantém o preview
desativado; habilitar com valor `1` somente em homologação supervisionada.
Jamais colar credenciais em prints, tickets ou commits; rotacionar credenciais expostas.

Autenticado como operador de suporte, os endpoints são:

- `GET /api/v1/tenants/{tenant_id}/ai/status`: indica configuração, sem chave.
- `POST /api/v1/tenants/{tenant_id}/ai/preview`: recebe `{"message":"Quanto custa cabelo e barba?","history":[]}`.

A prévia busca **exclusivamente** serviços ativos no tenant autenticado (RLS),
passa nomes/IDs de catálogo como dados para a IA e valida os IDs retornados.
Preços e tempos somados são calculados pelo backend. Datas interpretadas são
exibidas como NÃO VERIFICADAS; horários não são oferecidos sem motor de agenda.
O preview não grava histórico nem dados de clientes. O request pode carregar
até oito mensagens anteriores para contextualizar a classificação.

## Próximas etapas antes da automação real

- Configuração por estabelecimento e painel administrativo de teste.
- Ferramentas autenticadas de consulta de disponibilidade e profissionais.
- Estado conversacional persistente por empresa/canal/cliente, expiração e LGPD.
- Confirmação explícita do cliente, motor de agendamentos já existente, revalidação,
  idempotência e sincronização Calendar em sandbox antes de produção.
- Modo opt-in WhatsApp, handoff humano, cotas por tenant, auditoria e monitoramento.
- Testes reais da integração Groq e Calendar em ambiente de testes autorizado.

Esta entrega inicial nunca substitui o menu determinístico do WhatsApp;
mensagens reais continuam no fluxo anterior. `GROQ_API_KEY` sem
`GROQ_PREVIEW_ENABLED=1` não ativa chamadas externas.


## WhatsApp vinculado — fluxo real

Após CI e homologação, configure o serviço API com:
- GROQ_API_KEY (segredo privado), GROQ_PREVIEW_ENABLED=1;
- QR_AI_TENANT_ID com o UUID do estabelecimento REAL vinculado ao número;
- QR_AI_ENABLED=1 e QR_AI_ALLOWED_SENDERS com telefones completos 55... dos testadores, separados por vírgulas (recomendado antes de abrir ao público).
- A API já possui QR_BRIDGE_TOKEN compartilhado com o bridge.

Configure no serviço QR:
- QR_AI_ENABLED=1;
- QR_AI_API_URL=http://orion-assistente.railway.internal:8080/internal/qr/ai/reply.

No painel, abra o dispositivo conectado > Configurações > Chatbot e ative o
toggle. A ativação é uma opção cifrada persistida no volume; por padrão, continua
desligada. O bridge encaminha apenas mensagens privadas recebidas agora. Não
processa conversas anteriores, grupos, mensagens próprias ou campanhas.

A API registra estado conversacional temporário no Redis (30 minutos), busca
serviços e profissionais do tenant com RLS, verifica disponibilidade pelo motor
único de reservas, cria hold de cinco minutos e confirma **somente com SIM**.
Handoff suspende respostas automáticas por 30 minutos, permitindo atendimento
manual na aba Conversas. A prévia usa namespace de Redis separado, e não cria
reservas, eventos ou mensagens reais.

**Limites:** via WhatsApp vinculado não oficial (Baileys) e pode sofrer
dessincronização Signal; aceitação pelo servidor não equivale a entrega.
A sincronização Google Calendar real exige OAuth do estabelecimento e
GOOGLE_LIVE_SYNC=1, validado separadamente. Sem OAuth, a confirmação refere-se
apenas à reserva gravada no Orion, não a evento confirmado pelo Google.
A opção de conversas "pergunta" responde dados do catálogo disponível; FAQs e
políticas comerciais mais amplas requerem cadastro e suporte adicional.


Veja [Homologação avançada de IA](ai-validation.md) para áudio, nomes, histórico e remarcação.
