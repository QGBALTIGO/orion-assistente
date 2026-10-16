# Backlog e travas por fatia

Documento oficial: `Prompt_Mestre_Codex_SaaS_WhatsApp_Barbearias.md`
fornecido pelo proprietário fora do repositório (não versionar segredos).

- **FATIA 0 (base implementada/testada):** Fundação FastAPI, Docker, PostgreSQL 16, RLS,
  usuários separados, migrations, Arq, /health, testes reais e CI.
  Aceite pendente até CI/PostgreSQL real confirmar as verificações.
- **FATIA 1 (implementada na branch própria, pendente da CI):** catálogo,
  equipe, profissionais, serviços, horários, pausas, folgas, bloqueios,
  interface de suporte em pt-BR, autenticação Bearer e viewer restrito.
  Os papéis WhatsApp owner/barber/assistant são cadastrados, não autenticação WhatsApp.
  A autorização operacional não substitui contas de staff e sessões futuras.
- **FATIA 2 (em validação nesta branch):** agenda interna, holds, protocolo,
  snapshots, cancelamento/remarcação, serviço combinado e teste de 50 tentativas.
- **FATIA 3:** Meta WhatsApp Cloud API com autorização, webhooks assinados,
  máquina de estados e comandos administrativos permitidos.
- **FATIA 4:** Google Calendar OAuth opt-in, outbox/retries, exportação CSV.
- **FATIA 5:** piloto real controlado de UMA barbearia, mediante autorização.
- **FATIA 6:** landing comercial, onboarding e Asaas sandbox/assinaturas.
- **FATIA 7:** expansão com medição de capacidade para cerca de 15 empresas.
- **FATIA 8:** inbox humana, IA, CRM/campanhas autorizadas, automações e
  canais opcionais.

**Entrega técnica:** PRs dependentes da 0 até a 8, mediante autorização do
usuário para continuar. **Entrega comercial/produção:** condicionada ao
piloto real e às integrações externas autorizadas. Até lá, não ativar Meta,
Google, Asaas ou campanhas nem anunciar funcionalidades experimentais.
