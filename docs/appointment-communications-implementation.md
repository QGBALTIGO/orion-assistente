# Comunicação de agendamentos — 29/10/2026

Base auditada: `76e02ed9b650d8fc84dbb2e272312c78149ef832`.

## Inventário anterior às alterações

| Fluxo | Código real | Contrato a preservar |
|---|---|---|
| Reserva pública | `app/public_site_api.py:public_book` | cotação assinada, hold e confirmação persistidos antes da resposta |
| Agenda | `app/booking_service.py` | advisory lock por profissional, exclusão PostgreSQL de intervalos, snapshots e CAS |
| Identificação | `appointments.protocol`, `customers.whatsapp_e164`, IDs UUID | protocolo AG aleatório é identificador, nunca autorização |
| Profissionais | `professionals.phone_e164`, `professional_services` | vínculo e contato por tenant; nenhuma cópia automática ao dono |
| Cancelar/remarcar | `cancel_appointment`, `reschedule_appointment` | transação, histórico, idempotência e outbox Google |
| Conclusão | `management_models.transition_appointment` | finanças, comissões e fidelidade dependem da transição real |
| Conclusão por tempo | `auto_completion_worker.py` | recurso anterior opt-in conflita com este pedido; conclusão real deve prevalecer no novo fluxo |
| Canais QR | `qr_channels.py`, `qr_channel_scope.py`, bridge | credenciais por canal, leases, allowlist, sessão em volume `/data` |
| Ingresso | `qr_ai.run_locked`, `worker._event_answer` | deduplicação, guardas autenticadas e invalidação de propostas |
| Automação | `automation_runtime.py`, `automation_transport.py` | persistência PostgreSQL, consentimentos, resultado incerto sem reenvio cego |
| Papéis | `tenant_access.py`, `catalog_api.py` | owner/editor/viewer, admin/support; não há login individual vinculado a professional_id |
| Fusos | `tenants.timezone`, ZoneInfo, timestamptz | agenda em instantes UTC, exibição em IANA da empresa |
| Site | `management_site_preferences.public_slug`, `public_origins.py` | origem configurada no servidor; Host enviado não concede confiança |

## Estado inicial Railway

Projeto `55750961-99ca-495e-b55e-4c07d78e53df`, ambiente `162d0407-3d4f-4163-9cb2-0c90154c707f`.
API, worker, QR, Postgres e Redis online; sem mudanças de configuração no inventário.
Somente ambiente production existente. Testes sintéticos devem usar banco isolado.
Volume QR `96a57936-6572-4604-887b-a7c8123263ff` deve ser preservado.

## Regressão antes de avançar

- `tests/integration/test_booking.py`: agenda, conflitos, idempotência e concorrência.
- `tests/integration/test_public_catalog.py`: criação pública e isolamento.
- `tests/integration/test_appointment_lifecycle.py`: cancelamento/remarcação e auditoria.
- `tests/integration/test_automation_postgres.py`: persistência e consentimentos.
- `tests/integration/test_qr_single_connection_postgres.py`: sessão/canal canônico.
- `tests/integration/test_rls.py`: isolamento real PostgreSQL.
- `tests/unit/test_appointment_lifecycle_unit.py`, UI pública e testes do bridge.

## Sequência de integração

1. Tornar a autorização e o limite de uma hora parte do motor atual; ampliar entropia de novos protocolos sem invalidar antigos.
2. Acrescentar credenciais independentes com hash, revogação e validade; gestão pública sem informação no GET de prévia.
3. Integrar resposta segura ao protocolo e ação WhatsApp após persistência.
4. Acrescentar eventos transacionais e processamento persistente reutilizando as credenciais/recibos do transporte existente.
5. Integrar avaliação, configurações e acompanhamento nos componentes do Orion.
6. Executar regressões PostgreSQL/Redis, matriz de interface, concorrência e revisão de segurança antes de qualquer publicação.

O transporte não será ampliado além das permissões reais do canal. Testes reais de WhatsApp dependem de números explicitamente autorizados. Nenhum envio a cliente real é autorizado por este documento.
