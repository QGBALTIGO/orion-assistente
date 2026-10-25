# Finalização automática e Google Agenda — Orion

A agenda interna no PostgreSQL é a fonte da verdade; o Google é um espelho unilateral.
Uma falha do Google não desfaz agendamentos nem lançamentos.

## Finalização e segurança

O worker ARQ consulta os agendamentos confirmados cujo `end_at` já passou uma
vez por minuto. A implementação só finaliza quando a flag
`ORION_AUTO_COMPLETE_APPOINTMENTS=1` estiver ativa. Na primeira ativação,
registra um marco durável no Redis; horários que **já haviam terminado antes**
dessa ativação não são finalizados retroativamente. Paradas temporárias do
worker podem ser recuperadas a partir desse marco. Reiniciar o processo não
reinicia esse limite.

Para cada atendimento, a transição é transacional, com escopo de empresa,
lock de profissional, bloqueio de linha e chave determinística. Cancelamentos,
remarcações, registros de falta e finalizações manuais concorrentes prevalecem
corretamente; status diferente de `confirmed` nunca é finalizado em lote.
A regra usa o horário real `end_at` do agendamento, não o relógio do navegador.

Um atendimento finalizado emite histórico `source=system`, pontuação de
fidelidade existente, comissão calculada com snapshot, intenção de atualização
do Google e **um** lançamento de receita **pendente**, vinculado ao atendimento.
Não há confirmação fictícia de Pix/cartão/dinheiro; quem receber pode registrar
`paid` no Financeiro. Receita econômica de serviços continua vindo dos
agendamentos completados no DRE para evitar dupla contabilização.
Um atendimento zerado por desconto não cria cobrança pendente.

**Limitação de negócio:** a passagem do horário não prova presença física.
Estabelecimentos que precisam verificar comparecimento devem desativar a
flag e usar a finalização manual até implementar a confirmação real.

## Google Agenda

`GOOGLE_LIVE_SYNC=1` precisa estar habilitada **na API e no worker**, com as
mesmas credenciais Google e chave Fernet. A API expõe também a situação
do heartbeat do worker (`worker_online` e `worker_ready`) para distinguir
problemas de configuração de falhas de rede/permissão.

A cada minuto o worker procura intenções pendentes. Limites de requisições e
falhas transitórias continuam com retry automático de backoff limitado, sem
precisar esgotar sete tentativas e solicitar retry manual. Erros permanentes,
autorização vencida e agendas sem permissão continuam exigindo correção da
conta. O painel atualiza agenda, Google e resumos financeiros periodicamente
sem interromper formulários ou detalhes abertos.

Checklist de implantação: publicar o mesmo commit na API e no worker,
verificar o heartbeat, conferir a autorização Google real da empresa,
ativar `GOOGLE_LIVE_SYNC=1` nos dois serviços e por último ativar
`ORION_AUTO_COMPLETE_APPOINTMENTS=1` no worker. Não alterar a chave
Fernet existente.
