# Orion · Agenda · Clientes fixos

## Objetivo

Permitir ao responsável de uma empresa combinar com um cliente um profissional,
serviços e um horário periódico por um **número definido de visitas**, e reservar
esses horários como atendimentos normais do Orion, preservando o modelo visual,
o histórico, os lembretes, o Google Calendar, os serviços e o financeiro.

Exemplo: João, Corte, Peixoto, toda sexta às 14h, 12 visitas. Ao confirmar,
as 12 sextas são reservadas. O cliente não precisa agendar de novo a cada semana.

## Referências e decisões

- Trinks: repetição semanal, identificação e controle individual/série.
- AppBarber/AppBeleza: escolha de serviço, profissional, frequência e quantidade.
- Fresha: edição individual não deve alterar automaticamente todos os eventos.
- Regra RRULE/RFC 5545: **COUNT inclui a primeira ocorrência**.

Adaptamos comportamento, não identidade gráfica. Nenhuma página ou paleta
paralela foi criada: botão, aba, modal, campos e badges usam a suite Orion.

## Contrato funcional

1. A criação é **administrativa** e exige `agenda.manage`; não surge no site
   público ou no chatbot enquanto não houver decisão de produto.
2. Cada recorrência exige cliente, telefone, profissional, um a seis serviços,
   primeiro dia, horário, dia da semana, intervalo (1, 2, 4 semanas) e quantidade
   (2–52, desde que todas as ocorrências caibam em até 365 dias).
3. A prévia retorna todas as datas e explica cada horário incompatível.
   Qualquer conflito impede a criação de **toda a série**. Não há reserva parcial.
4. A operação real utiliza `tenant_transaction`, lock do profissional,
   `check_slot` do Orion, exclusão GiST no banco e idempotência. Todas as
   ocorrências passam a `confirmed`, com um protocolo próprio.
5. Cada visita possui snapshots de itens, preço, duração e buffers; cada uma
   possui histórico, ciclo de vida e sincronização de calendário próprios.
6. Cancele ou remarque individualmente pelo detalhe da visita. O vínculo de
   série não muda as regras de cancelamento. O encerramento administrativo da
   série cancela somente visitas futuras ainda confirmadas; atendimento passado,
   concluído e financeiro são preservados.
7. Comunicação: criação em lote evita enviar dezenas de avisos de confirmação
   imediatamente ao profissional. Há um aviso para a primeira visita; lembretes
   de uma hora são enfileirados por visita, se a empresa estiver configurada.
   Cancelamento da série não dispara spam de cancelamento em massa.
8. Isolamento obrigatório por `tenant_id`, FK composta, FORCE RLS e permissões
   herdadas de `agenda.manage`.
9. O recurso não muda a grade padrão ou o horizonte de reservas oferecido a
   clientes do site público.

## Interface

**Agenda** → **Novo agendamento** → opção **Cliente fixo**. A seção **Clientes**
mostra as abas **Ativos**, **Fixos** e **Em risco** no nicho de barbearia.
Em **Fixos**, o proprietário consulta as séries futuras ativas e pode
editar o dia, hora, profissional, serviços, frequência e próximas visitas,
sem duplicar o cliente no cadastro. O formulário mostra clientes existentes (ou cadastro novo),
serviços, profissional, dia, primeiro atendimento, horário, periodicidade
e quantidade. A primeira confirmação visual é uma prévia com conflitos;
a segunda registra o lote inteiro.

A lista resume o cliente, profissional, frequência, serviço, quantidade e
próxima visita. Detalhe abre cada visita no formulário já existente, com
opção de encerrar todos os futuros horários.

### Alteração de recorrência

- A edição opera somente sobre visitas **futuras confirmadas**. Histórico,
  serviços anteriores, visitas já executadas e lançamentos financeiros são preservados.
- A alteração gera uma **nova série** vinculada ao mesmo `customer_id`; a série
  anterior é encerrada e suas visitas futuras são canceladas em uma **transação única**.
- A prévia ignora os horários da série antiga sem gravar cancelamentos, verifica
  todos os novos horários e exige confirmação. Um conflito aborta a alteração
  inteira, preservando os agendamentos anteriores.
- Frequência semanal: toda semana, a cada duas ou a cada quatro semanas.
  Frequência mensal: **exatamente 2 vezes por mês** (1ª/3ª ou 2ª/4ª semana)
  ou **4 vezes por mês** (primeiras quatro semanas), sem marcar a quinta semana
  em meses longos.
- A revisão de esquema 0075 adiciona `monthly_visits` opcional; a migração
  exige backup verificado, contrato RLS e validação read-only do runtime.


## Qualidade e publicação

- Unitários: contagem, frequência, data, limites, timezone/DST, serviços,
  validação de idempotência.
- PostgreSQL real: criação atômica, colisão com reserva existente, idempotência,
  RLS entre empresas, leitura pelo endpoint normal, encerramento e histórico.
- DOM/JavaScript: aba, formulário, prévia antes da gravação, conflito impedindo
  envio e confirmação de encerramento.
- Executar a CI completa existente com PostgreSQL 16, Redis, Node e Docker,
  além dos novos testes.
- Validar visualmente em 320, 375, 768, 1024 e 1440 pixels, nos temas claro
  e escuro. Fazer smoke-test em empresa de homologação, sem enviar avisos reais
  a clientes e sem mexer em agendas de produção.
- **Somente depois** aplicar migração em ambiente de homologação e avaliar
  lançamento gradual na produção.

Esta documentação descreve o que foi proposto/implementado na branch.
Ela não é evidência de aprovação dos testes ou de publicação.
