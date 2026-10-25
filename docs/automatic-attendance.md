# Finalização automática e Google Agenda — Orion

A agenda interna no PostgreSQL é a fonte da verdade; o Google é um espelho
unilateral. Uma falha externa não apaga nem altera agendamentos internos.

## Finalização segura de atendimentos

Após a migração aditiva `0031_auto_complete_appointments`, o PostgreSQL
mantém `tenants.auto_complete_after` como limite de ativação prospectiva
por empresa. Na primeira instalação, horários encerrados antes da migração
nunca são convertidos automaticamente em receita.

O worker ARQ verifica a cada 10 segundos os atendimentos confirmados cujo
`end_at` terminou. Com `ORION_AUTO_COMPLETE_APPOINTMENTS=1` no worker,
`public.orion_due_auto_completions` lista somente IDs de agendamento/empresa.
Essa função de banco de dados permite descoberta limitada ao usuário
`app_support` sem conceder leitura de dados dos clientes. Cada conclusão
é executada com `app_runtime`, RLS por empresa, bloqueio de profissional,
lock de linha, estado esperado e chave idempotente; alterações concorrentes
não são sobrescritas. Não exige a página aberta.

A transição preserva a trilha com origem `system`, comissões, descontos,
fidelidade e outbox do Google. Atendimentos finalizados automaticamente criam **uma conta a
receber pendente** no Financeiro, sem alegar Pix, cartão ou dinheiro recebido.
O DRE já contabiliza o serviço a partir do agendamento, sem duplicar receitas.
Recebimentos reais devem ser baixados como pagos; atendimentos gratuitos
não criam cobrança. Agenda, Dashboard, Desempenho e Financeiro renovam
informações sem interromper diálogos e formulários.

**Limitação:** horário passado não confirma presença física. Empresas que
precisam de verificação presencial devem manter a flag desabilitada.

## Google Agenda

O worker procura eventos pendentes a cada minuto e publica seu heartbeat
no Redis, exposto de maneira não sensível pela API. Falhas transitórias
continuam sendo retentadas automaticamente com backoff limitado.
Autorização revogada, permissões insuficientes e falhas de configuração
exigem correção manual. `GOOGLE_LIVE_SYNC=1` deve estar ativo na API e
no worker, com as mesmas credenciais/fernet existentes. O Google não
edita o registro de origem no Orion.

Implantação: (1) rodar `0031` após validação de backup e papel migrador,
(2) publicar a API e o worker no mesmo SHA, (3) conferir heartbeat e
Google OAuth de uma conta real, (4) ativar `GOOGLE_LIVE_SYNC=1` nos dois
serviços, (5) ativar `ORION_AUTO_COMPLETE_APPOINTMENTS=1` só no worker.
Nunca sobrescrever `INTEGRATION_FERNET_KEY`.
