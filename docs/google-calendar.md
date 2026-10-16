# FATIA 4 — Google Calendar opt-in e CSV

O cadastro, os agendamentos e a disponibilidade NÃO dependem do Google.
A conexão OAuth usa escopo mínimo `calendar.events`, autorização por
estabelecimento, `state` de uso único com TTL no Redis e refresh token
cifrado com Fernet. Não solicitar senha do Google.

Configure em variáveis privadas GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
GOOGLE_REDIRECT_URI HTTPS e INTEGRATION_FERNET_KEY. A API retorna URL
de autorização somente para suporte autenticado. A callback valida state,
troca código por tokens e persiste só o refresh token criptografado.
Por padrão GOOGLE_LIVE_SYNC=0: **não envia eventos reais**.

No mesmo commit que confirma/cancela/remarca reserva, o banco mantém a
entrada `calendar_outbox`, única por agendamento/tenant, para
sincronização assíncrona. Criação/atualização/exclusão usam ID estável
derivado do UUID da reserva. O worker não cancela agendamento em caso de
falha externa; faz retry exponencial e expõe pendência/erro sanitizado.
Revogação local bloqueia uso do token; reautorização permite retomar.

Calendar API oficial:
https://developers.google.com/workspace/calendar/api/v3/reference/events
Sem credenciais e calendário real, testes usam FakeCalendar com calendários
distintos, falha injetada e revogação. OAuth vivo e evento real continuam
pendentes de autorização e validação.

CSV: GET /api/v1/tenants/{id}/export/appointments.csv. Download exige
token operacional válido e escopo do tenant. Cabeçalhos em português,
UTF-8 BOM, separador ; e neutralização de fórmulas para Excel.
Não expõe registros de outras empresas e não substitui a fonte Postgres.

**Ainda pendente**: implementar observabilidade operacional de retries,
políticas de retenção e exportação/anonimização LGPD completa, rotação
de chaves com versionamento e backups criptografados.
