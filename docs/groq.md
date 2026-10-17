# Groq no Orion — Etapa inicial: intenção e simulação segura

A integração utiliza a API de Chat Completions da Groq com JSON Object Mode.
**Estado: preview sem envio, sem alteração de reservas, sem integração automática
com o WhatsApp e sem conexão Google Calendar disparada pela IA.**

## Configuração

Definir como segredo no backend `GROQ_API_KEY` (nunca no frontend e nunca em Git).
O modelo padrão é `llama-3.3-70b-versatile`, ajustável por `GROQ_MODEL` para um dos
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
