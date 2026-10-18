# Agenda e ciclo do atendimento

## Comportamento do produto

A Agenda usa os agendamentos reais do estabelecimento e o fuso configurado para
ele. A lista e a grade compartilham a mesma seleção de dia, profissional,
situação e busca. O detalhe aberto pela lista ou pela grade consulta novamente
o servidor antes de apresentar as ações disponíveis.

| Situação apresentada | Estado persistido | Comportamento |
| --- | --- | --- |
| Reserva temporária | `hold` | Ocupa o horário durante o prazo de confirmação. Uma reserva expirada não pode ser confirmada. |
| Confirmado | `confirmed` | Mantém o horário ocupado e permite remarcação ou cancelamento autorizado. |
| Cancelado pelo cliente | `cancelled`, `requested_by=customer` | Libera o horário e preserva o histórico e o motivo informado. |
| Cancelado pelo profissional | `cancelled`, `requested_by=professional` | Libera o horário e registra que o cancelamento foi solicitado pelo profissional. |
| Cancelado pela empresa | `cancelled`, `requested_by=business` | Registra um cancelamento operacional sem atribuí-lo ao cliente ou ao profissional. |
| Não compareceu | `no_show` | Só pode ser registrado depois do início previsto de um atendimento confirmado. |
| Finalizado | `completed` | Preserva o fluxo existente de descontos, comissão e fidelidade. Não equivale à confirmação de um pagamento. |

O responsável que solicitou o cancelamento e a pessoa que o registrou no painel
são informações distintas. O painel envia apenas a classificação e o motivo;
o servidor determina a conta, o papel e a origem autenticados. Cancelamentos
confirmados pelo próprio contato no WhatsApp são registrados como solicitações
do cliente. Não são enviadas novas mensagens pelo simples uso das ações da Agenda.

Eventos antigos não permitem reconstruir sua autoria com segurança. Eles
continuam com `unknown` na apresentação, sem atribuir retroativamente um
cancelamento a uma pessoa. Reservas vencidas são identificadas como expiração.

## Concorrência e histórico

As alterações continuam usando o lock do profissional e a restrição PostgreSQL
que impede sobreposição. O painel envia `expected_updated_at` e uma chave de
operação para impedir que um detalhe antigo sobrescreva uma alteração recente.
Tentativas com intenção diferente e a mesma chave são recusadas. A recuperação
de uma resposta perdida reutiliza a intenção original.

Confirmação, cancelamento, remarcação, falta e conclusão persistem o evento e a
intenção de sincronização no mesmo commit do agendamento. Uma falha no Google
não cancela uma reserva no Orion. A conclusão mantém as regras transacionais já
existentes para descontos, comissão e fidelidade.

O histórico estruturado fica em `appointment_history.details`, um objeto JSON
limitado a 8 KiB. As informações incluem versão do formato, solicitante, origem,
ator verificado, motivo opcional e estado/horário anterior. O motivo tem limite
de 1.000 caracteres e não é exportado para o Google.

## Google Agenda

A direção de sincronização é **Orion para Google Agenda**. Alterações feitas
diretamente no Google não alteram o estado de um atendimento no Orion nem
passam a bloquear automaticamente sua disponibilidade. O usuário deve usar o
Orion para confirmar, remarcar, cancelar, registrar falta e finalizar.

O proprietário e a administração podem autorizar o Google, escolher um
calendário gravável, reconectar e desconectar. A consulta respeita o acesso ao
estabelecimento. A conexão, a configuração do servidor e a situação da fila de
sincronização são apresentadas separadamente.

Na primeira autorização, o usuário escolhe a agenda. Depois que o vínculo está
estabelecido, a reconexão preserva a agenda canônica. A troca para outra agenda
com eventos já vinculados exige um fluxo específico de migração, para não deixar
compromissos órfãos. Este recurso não faz essa migração silenciosamente.

A primeira conexão inclui os atendimentos atuais e futuros. Atendimentos
encerrados antes dessa conexão, ainda sem vínculo ou tentativa de envio, ficam
identificados como anteriores à conexão. Eles não são apresentados como enviados
nem como falhas. Eventos já vinculados e tentativas de entrega incerta continuam
no fluxo de recuperação. Uma remarcação para um horário futuro torna o
atendimento elegível novamente.

| Alteração no Orion | Resultado no Google |
| --- | --- |
| Confirmar | Cria ou atualiza o evento próprio do Orion com identificação do atendimento. |
| Remarcar | Atualiza o mesmo evento com o novo horário. |
| Cancelar | Remove o evento próprio do Orion. O histórico permanece no Orion. |
| Registrar falta | Identifica a falta e deixa o compromisso transparente para a disponibilidade do Google. |
| Finalizar | Identifica a conclusão do atendimento. |

A integração não adiciona convidados nem envia convites ou notificações de
eventos. Telefone, observações internas, motivo do cancelamento e dados da conta
não fazem parte do evento exportado. Consulte também
[o guia de configuração do Google](google-calendar.md).

## Migração e implantação

A revisão `0019_appointment_lifecycle` parte exclusivamente de
`0018_sales_requests` e acrescenta:

- `appointment_history.details jsonb NOT NULL DEFAULT '{}'::jsonb`;
- o CHECK `ck_appointment_history_details`;
- o índice `ix_appointment_history_timeline`.

Não muda o status de nenhum agendamento existente. Os eventos antigos recebem
apenas o objeto vazio como valor padrão da nova coluna. Nenhuma tabela nova,
permissão ou credencial é criada por essa migração.

O runner `scripts.one_time_agenda_migration` verifica a identidade exata do
banco, PostgreSQL 16, papel efetivo `app_migrator`, catálogo, permissões,
políticas RLS e revisão. Somente o arquivo de migração selado pode executar.
Toda a alteração e a atualização da revisão usam uma transação; falhas
revertem a operação. O replay em `0019` é exclusivamente de leitura.

| Verificação selada | SHA-256 |
| --- | --- |
| Arquivo da migração | `50c942f69b661769a6cb9d9d7c2916178c0510f448eae87209f9d56dbef98c57` |
| Catálogo anterior do histórico | `0466f1ca161c4335510b94109f023f31709f0bc0361984d8f1f8d0ef77288aaf` |
| Catálogo novo do histórico | `442893de1fbeb098b9f938926831ae4225e1314d900fa2ab0ee49f467e420ee8` |

Para o deploy da API, preservar as credenciais de migração e as identidades
esperadas já configuradas. Ativar `ORION_RUN_AGENDA_MIGRATION=1` e
`ORION_MIGRATION_SWITCH_ROLE=1`. Desativar a flag anterior
`ORION_RUN_SALES_REQUESTS_MIGRATION=0` e substituir o comando temporário por:

```sh
python -m scripts.one_time_agenda_migration && python -m scripts.probe_qr_bridge
```

A API e o worker precisam receber o mesmo commit desta atualização. O worker
usa o seguinte pre-deploy, com timeout da plataforma de 120 segundos:

```sh
python -m scripts.wait_for_agenda_schema
```

Esse comando espera no máximo 90 segundos pela coluna do histórico. Ele usa
`APP_DATABASE_URL` como `app_runtime`, apenas consulta o catálogo PostgreSQL e
nunca executa migrações. Assim, o worker pode aguardar a transação da API quando
os dois serviços forem publicados juntos. Cada tentativa tem limite de três
segundos; falhas de identidade ou configuração interrompem a espera. O launcher
da API remove as variáveis de migração antes de iniciar o processo HTTP. Ele
também desativa o access log padrão do Uvicorn para que o código de autorização
e o estado recebidos na query do callback não sejam registrados pelo processo.
Os logs de erro e os registros explícitos da aplicação continuam disponíveis.

Depois de confirmar a revisão `0019` e a saúde dos serviços, remover a flag de
execução única ou deixá-la em `0`, e restaurar o pre-deploy apenas para
`python -m scripts.probe_qr_bridge`. Nunca restaurar o runner anterior como
etapa permanente: ele é selado para uma revisão diferente. O gate de leitura do
worker pode ser removido depois da conferência da revisão e da saúde dos dois
serviços; ele não mantém credenciais de migração no worker.

## Validação

Os ensaios do runner usam bancos com nome aleatório em PostgreSQL local,
incluindo eventos sintéticos preexistentes. Os testes comparam os dados e o
catálogo anterior, recusam fonte incompatível, simulam falha depois da alteração
da revisão e validam que o replay não grava dados.

As verificações da interface usam o DOM isolado do painel, com dados sintéticos,
e os testes Google usam transporte simulado. Eles não enviam compromissos nem
convites para uma conta real. Uma autorização OAuth real e a conferência visual
no navegador autenticado continuam necessárias para validar o ambiente final.
