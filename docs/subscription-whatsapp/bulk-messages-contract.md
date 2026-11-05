# Etapa 09 — contrato de Mensagem em Massa no Orion

Contrato de implementação definido após a observação em 2026-11-05. A etapa
08 foi encerrada antes deste trabalho. Este documento especifica decisões
do Orion; a evidência da referência está no registro de observação separado.

## Escopo e superfície

- Disponível somente ao proprietário da empresa ativa, no nicho
  `servico_assinaturas` e workspace `subscription_services`.
- Rota do painel `massa`, feature `bulk_messages.manage` e módulo
  `OrionSubscriptionBulkMessages`, usando tokens, fonte, campos, tabelas,
  diálogos, temas e navegação existentes. Sem identidade visual do BotBot.
- API `/api/v1/tenants/{tenant_id}/subscription-whatsapp/bulk-messages`.
- Lote imediato, independente de Campanhas e Programadas; sem cron novo,
  agendamento, título obrigatório, janela horária ou recorrência.
- Nenhum provedor, plano ou complemento novo pago.

## Preparação das linhas

A entrada apresenta os três totais de mensagens enviadas e o histórico de
transações. “Mensagem em Massa” abre uma bancada com adição de destinatários,
progresso, intervalo e ação Iniciar. O diálogo usa um dispositivo por adição,
as abas Grupos/Contatos/Entrada Manual e texto ou template da biblioteca.

Os grupos são os já cadastrados em Campanhas; o catálogo é o mesmo de
Programadas. Grupos e contatos são resolvidos pelo servidor e deduplicados
dentro de cada seleção. A entrada manual aceita um telefone por linha com
código do país, sem criar clientes, grupos ou permissões implicitamente.

Cada linha tem identidade própria, dispositivo, origem e conteúdo. A bancada
permite alterar o texto individual ou remover a linha antes da revisão.
Templates usam as oito modalidades existentes e suas sequências já definidas.
Linhas manuais repetidas e novas adições ao mesmo telefone permanecem distintas,
como na preparação observada; a revisão informa quantas mensagens repetem o
destinatário. Não se deduplicam silenciosamente conteúdos diferentes.

O limite do Orion é de **500 linhas no lote**, contando repetições. Um excesso
é rejeitado com mensagem clara, sem truncamento silencioso. Telefones devem
ter 8–15 dígitos e código de país; pontuação comum é normalizada. A aceitação
de `0` observada no BotBot não é replicada. Texto simples tem até 1.000 unidades
UTF-16, coerente com o limite do campo. Marcações comuns do WhatsApp são texto
inerte na interface; não é permitido HTML ativo. O contrato não presume
substituição de variáveis no texto simples da referência.

O intervalo é um inteiro de 10 a 60 segundos, padrão 10. É um mínimo entre
sequências de destinatários; limites existentes do transporte podem alongá-lo.
Não se oferece estimativa exata nem promessa de entrega/leitura.

## Identidade, revisão e confirmação

A preparação não revisada permanece somente em memória e é descartada ao
recarregar, como observado. Leituras de opções, contatos e audiência não criam
lote nem efeitos de envio. Não se guardam telefones ou conteúdo no localStorage.

O payload final da bancada contém `interval_seconds` e `items`, em ordem:

```json
{
  "row_key": "UUID da linha",
  "device_id": "UUID do dispositivo",
  "source": "catalog ou manual",
  "contact_key": "phone:digitos, somente para catalog",
  "phone": "digitos, somente para manual",
  "message_type": "text ou template",
  "text": "somente para text",
  "template_id": "UUID, somente para template"
}
```

Campos condicionais são mutuamente exclusivos, e `row_key` não se repete.
Nome, número do remetente, grants, geração, permissões, fontes, planos, versões
e disponibilidade vêm do servidor; o browser não os determina como autoridade.

Iniciar prepara uma revisão persistida por chave idempotente, válida por
120 segundos. Ela mostra destinatários, repetições, conteúdo/partes e bloqueios.
Confirmar exige `ENVIAR`, consentimento explícito, a mesma chave e a versão da
revisão. Somente a confirmação válida cria o lote e seus destinatários.
Uma resposta perdida é recuperada pelo pedido existente, inclusive após reload;
uma consulta negativa não autoriza trocar automaticamente a chave ou reenviar.

Pedidos e histórico são vinculados a empresa e conta. Mudança de conta,
empresa, permissão ou logout invalida leituras, limpa conteúdo privado e
preserva somente a identidade mínima de uma operação já despachada no contexto
correto. AbortSignal é usado para leituras; abortar uma espera de escrita não
é interpretado como cancelamento de uma operação que pode ter sido confirmada.

## Execução e resultados

Após a confirmação, o lote persiste no servidor e continua pelo poll privado
do dispositivo. A continuidade após fechar a página é uma decisão explícita
do Orion: a referência não foi observada durante um envio real.

- Lote de execução única; estados pending/running/completed/failed/uncertain/cancelled.
- Linha: pending/running/sent/failed/uncertain/cancelled. Uma sequência com
  várias partes só é enviada por completo quando todas foram aceitas.
- A ordem das linhas é conservada; dispositivo indisponível não autoriza
  transferir a linha a outro dispositivo.
- Conteúdo, template, origem, dispositivo e versão de permissão são congelados
  na revisão e revalidados na confirmação e em cada admissão/parte.
- Origem manual depende da autorização explícita do lote e nunca supera um
  opt-out existente. Origem de catálogo congela fontes e não ganha autorização
  de uma fonte adicionada depois. O próprio número verificado do dispositivo
  não recebe a mensagem de teste/campanha do lote.
- Compartilha a serialização por empresa, seis segundos entre sequências,
  dez admissões por minuto e a capacidade já existente por canal/parte.
- Efeitos criados somente quando admitidos; prazo curto e vínculo a lote,
  destinatário, plano e parte. Sem milhares de efeitos futuros antecipados.
- Recibos e tentativas são preservados. Resultado incerto não provoca repetição
  automática nem reenvio pelo histórico genérico.
- Cancelar encerra somente trabalho pendente, exige versão atual e chave
  idempotente; não apaga histórico ou desfaz um envio aceito.
- Efeitos encerrados fora do módulo, por configuração/remoção de dispositivo,
  são reconciliados em toda a empresa antes de verificar a fila compartilhada.

Não se acrescenta uma ação de pausa/reinício com semântica presumida da
referência. Acompanhamento e cancelamento preservam o padrão operacional do Orion.

## Persistência e transporte

Três relações privadas: `subscription_bulk_batches`,
`subscription_bulk_recipients`, `subscription_bulk_requests`. Identidades e
FKs compostas vinculam tenant, lote, destinatário e efeito. A ordem e `row_key`
são únicos no lote; o telefone não é único porque linhas repetidas são explícitas.

Evento `bulk`, efeito/purpose `bulk_action`; colunas de vínculo `bulk_id`,
`bulk_recipient_id` e `bulk_part_index`. A autorização inclui o hash da revisão,
plano, índice de parte e contexto atual do dispositivo. Assets privados exigem
essa mesma autoridade antes e depois da leitura.

Migração `0070_subscription_bulk`, após `0069_subscription_schedules`.
RLS forçada, owner/grants mínimos e sem DELETE de histórico. Não ampliar os
grants de clientes além das sete colunas já concedidas na etapa 08. O predeploy
exige backup privado verificado antes da DDL; API/worker aguardam o catálogo
com sondas somente leitura e sem credenciais temporárias.

## Histórico e verificação

Os totais contam linhas integralmente aceitas, não partes: todas, últimos
30 dias corridos e dia atual no fuso da empresa. Isso explicita a decisão de
período que não foi deduzida dos registros antigos da referência. A lista
conserva remetente, destinatário, tipo, template e data, com estados operacionais
claros e acesso ao lote para acompanhar o progresso. Mensagens Enviadas também
identifica a origem Mensagem em Massa, sem reenvio genérico do mesmo efeito.

Validação: políticas/limites, API/CSRF/nicho/owner, idempotência e recuperação,
origens e opt-out, concorrência e duplicatas explícitas, templates/arquivos,
cadência compartilhada, recibos/resultado incerto, cancelamento, DDL/RLS/grants,
ponte sintética, DOM e Chrome/WebKit nos temas/tamanhos existentes. Testes não
usam destinatários, dados nem provedores reais. Após a publicação, conferir
saúde dos serviços e a interface conectada sem confirmar envio de produção.

A etapa 10 só começa depois do encerramento desta etapa.
