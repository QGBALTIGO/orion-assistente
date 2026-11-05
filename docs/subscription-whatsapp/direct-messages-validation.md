# Enviar Mensagem — qualificação da etapa 11

## Estado

**Implementação em qualificação, sem publicação**, em 05/11/2026.
A continuação usa a [PR 263](https://github.com/QGBALTIGO/orion-assistente/pull/263),
inicialmente aberta para o registro da etapa 09 e do bloqueio da etapa 10.
O usuário autorizou manter funções bloqueadas por plano como pendentes e
prosseguir pelas acessíveis, sem compras ou nova confirmação por esse motivo.

Base de produção: `d17cb67a29fca8293299ac3b2b969601b540075a`, árvore
`505fd9e7b588dac70bd5c325b5c8d9e063aa47d1`, após Mensagem em Massa.
O contrato e os limites de observação estão nos registros
[de observação](direct-messages-observation.md) e [de implementação](direct-messages-contract.md).

## Verificações durante a implementação

- Permissões e rotas: o teste de empresa inativa identificou a necessidade
  de fechar também a nova funcionalidade. A política mantém somente Conta
  e configuração disponíveis nessa condição, preservando a matriz dos outros
  nichos. Os 77 casos focados de feature/workspace passaram; o conjunto final
  de wiring e classificação de rotas passou em 59 casos, e o shell DOM em 15.
- Backend: 418 casos focados passaram, incluindo 97 novos de política, API,
  snapshot, recuperação, cancelamento e autoridade do envio direto, mais as
  regressões de Massa, Templates e Mensagens Enviadas selecionadas.
- Publicação: 568 casos focados passaram para backup, cadeia de migração,
  barreiras de catálogo e inicialização, incluindo os ancestrais afetados.
- Texto: a revisão de integração encontrou remoção de espaços por um helper
  compartilhado. Enviar Mensagem passou a preservar seu texto literal;
  Templates conserva sua normalização existente.
- Interface: os rascunhos foram separados por aba para preservar os campos
  ao alternar Texto, Mídia e Template, sem misturar modalidades no payload.
  Os 73 casos DOM focados passaram, incluindo os oito tipos de template,
  os 15 formatos aceitos no upload, o retorno do nome canônico do arquivo,
  a recuperação de pedidos e a rejeição comprovada do cancelamento obsoleto.
- Chrome local: 15 cenários passaram na primeira execução. O cenário restante
  aguardava um endpoint diferente do utilizado por Minha Conta; a expectativa
  foi corrigida para `/account/business-identity` e esse caso passou isoladamente.
  Não se apresenta esse resultado como uma única execução integrada de 16 casos.
  O WebKit local não está instalado e sua execução fica a cargo do CI.

Essas contagens são execuções locais sobrepostas, realizadas durante a
implementação, e **não são somadas** nem substituem a qualificação da árvore
final. Nenhuma delas é um teste de envio real do WhatsApp.

## Qualificação necessária da candidata final

O workflow dedicado deve executar os seis grupos existentes, incluindo os
novos contratos de Enviar Mensagem:

1. API, políticas, execução, arquivos reais e publicação.
2. PostgreSQL real descartável: migração 0071, origem imutável, RLS, papéis,
   integridade, transações, concorrência, idempotência e cadência compartilhada.
3. DOM, formulários, foco, estados de erro, navegação e isolamento de contexto.
4. Ponte, preservando o protocolo 1 e os formatos existentes de `bulk_action`.
5. Chrome, nos tamanhos e temas existentes.
6. WebKit, nos mesmos fluxos.

O PostgreSQL inclui confirmação e gravação opcional de template na mesma
transação, rollback, repetição da mesma chave, colisão de chave entre Massa
e Enviar Mensagem, dez/onze contatos, mídia e áudio, origem do histórico,
cancelamento e revogação antes de ler conteúdo privado.

O CI geral mantém falhas históricas. Sua comparação deve discriminar resultados
completos, falhas de infraestrutura e execuções incompletas; não se presume
aprovação global a partir do workflow dedicado.

## Critérios de publicação e encerramento

A migração `0071_subscription_direct` acrescenta a origem em lotes e pedidos,
com restrições e FK composta; não cria outro transporte. A publicação exige
backup privado verificado antes da DDL, confirmação da árvore qualificada e
barreiras somente leitura na API e no worker. A ponte mantém o protocolo e
os formatos já publicados; não precisa reconstruir a semântica do envio direto.

Após publicar, conferir a saúde dos serviços e abrir Enviar Mensagem no Chrome
conectado, percorrendo as três abas e seus campos no padrão visual do Orion.
Não enviar mensagem real, criar template, confirmar pedido ou alterar contatos
na conferência de produção. A etapa 12 começa depois desse encerramento.

**Pendente:** resultados da candidata final no CI, publicação, revisão de
deployments e conferência da interface ao vivo. A etapa 11 não está encerrada.
