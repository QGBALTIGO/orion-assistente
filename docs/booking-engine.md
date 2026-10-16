# FATIA 2 — Motor de agenda interno

PostgreSQL 16 é a fonte de verdade. Nenhum canal envia SQL de reserva:
todos passam pelo serviço de domínio book_appointment (hold de 5 minutos).

## Regras da disponibilidade

Soma da duração e preço de 1 a 6 serviços habilitados para o profissional.
Preparação e limpeza são somadas e bloqueiam o tempo anterior/posterior.
Grade inicial 15 min; aviso mínimo 30 min; horizonte de 60 dias.
Os três parâmetros são configuráveis em tenants. Verifica expediente,
pausas, folgas, bloqueios, profissional ativo e serviços ativos.
Usa fuso IANA por estabelecimento; armazena UTC/timestamptz.
Um horário não é ofertado se a duração e os buffers não cabem.
Horários inexistentes ou ambíguos em mudanças de fuso são recusados.

## Garantias transacionais

Constraint GiST com btree_gist impede sobreposição de intervalos ocupados
no mesmo estabelecimento e profissional, mesmo sob concorrência.
Advisory lock por profissional coordena escrita/expiração de holds;
não substitui a constraint. Hold vence após cinco minutos; tentativa
nova limpa holds vencidos antes de consultar o intervalo. Confirmação
após expirar não prossegue. Idempotency key e fingerprint rejeitam replay
com payload diferente. Protocolo AG-XXXXXX único não é autenticação.
Reservas registram snapshots dos serviços e histórico auditável.
Cancelamento libera o horário; remarcação atômica mantém ID e protocolo.
Ao remarcar, o preço/duração são recalculados usando catálogo atual.

## Rotas internas (não são do cliente final)

Autorização: Bearer token operacional configurado na FATIA 1.
GET /api/v1/tenants/{id}/availability?professional_id=UUID&day=AAAA-MM-DD&service_ids=UUID
POST /api/v1/tenants/{id}/appointments/hold
POST /api/v1/tenants/{id}/appointments/{booking_id}/confirm
POST /api/v1/tenants/{id}/appointments/{booking_id}/cancel
POST /api/v1/tenants/{id}/appointments/{booking_id}/reschedule
GET /api/v1/tenants/{id}/appointments/{booking_id}
GET /api/v1/tenants/{id}/appointments?day=AAAA-MM-DD

Exemplo de payload de hold, apenas dados de demonstração sintéticos:

    {
      "professional_id": "00000000-0000-4000-8000-000000000001",
      "service_ids": ["00000000-0000-4000-8000-000000000002"],
      "customer_name": "Cliente de teste",
      "customer_phone": "+5567999999999",
      "start_at": "2026-12-10T14:00:00-04:00",
      "idempotency_key": "pedido-teste-0001"
    }

## Pendências e limites

Não existe WhatsApp, Google Calendar, cobrança ou bot nesta fatia.
Não há feriado automático, nem limpeza periódica por worker: holds
vencidos são cancelados transacionalmente quando o horário é requisitado.
Alterações de expediente e folgas após reserva confirmada podem gerar
necessidade de reparo manual; bloqueio de novas confirmações é verificado.
A autorização por token de suporte não é autenticação final de clientes
e o painel não deve ser publicado antes de controles de produção.
É necessário provisionar CREATE no banco para app_migrator antes da
migration 0003, pois a extensão btree_gist é criada pela migration.
Este GRANT não deve ser dado ao papel de runtime.

## Verificação

docker compose --profile tools run --rm --build migrate
docker compose --profile tools run --rm --build test

Os testes usam PostgreSQL real, inclusive 50 tentativas simultâneas de
reserva para o mesmo profissional/horário com exatamente um vencedor.
CI e Docker Compose precisam passar antes do aceite da FATIA 2.
