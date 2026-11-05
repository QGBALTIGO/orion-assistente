# Etapa 09 — validação de Mensagem em Massa

## Estado

Implementação em qualificação, em 2026-11-05. A etapa 09 ainda não foi
publicada. A base de produção é
`149c43fba44b3f93bdcd90920751d79dc231d32a`, árvore
`36391408683510b62029c94077c85002a98c349e`, após o encerramento de
Mensagens Programadas e seu ajuste de texto nas PRs 260 e 261.

O escopo e as decisões próprias do Orion estão no
[contrato](bulk-messages-contract.md). Os comportamentos efetivamente vistos
no Chrome conectado e seus limites estão na
[observação](bulk-messages-observation.md).

## Evidência local

| Grupo | Resultado | O que verifica |
| --- | --- | --- |
| API, schemas, políticas e persistência do módulo | 116 passaram | Limites, texto literal, oito templates, linhas repetidas, identidade da revisão, confirmação, recuperação, cancelamento e projeções. |
| Integração do módulo com permissões, rotas, broker e histórico | 42 passaram | Restrição ao nicho, ordem justa de admissão, limpeza anterior aos gates, delegação de autoridade e ausência de reenvio genérico. |
| Publicação, reinicialização e runtimes compartilhados | 536 passaram | Backup antes de DDL, ancestralidade, cadeia de barreiras, reinício na revisão 0070, opt-out e preservação de Campanhas e Programadas. |
| Ponte de Massa, Programadas, Campanhas e falhas compartilhadas | 108 passaram | Transporte sintético, recibos, autorização por parte, incerteza, capacidades compartilhadas e assets privados. |

Os grupos acima têm interseções com outras execuções focadas e não são
somados para produzir um total único. A compilação Python, as verificações
de sintaxe JavaScript e a verificação de whitespace foram executadas nos
arquivos concluídos; o gate integrado será repetido sobre a árvore final.

Os testes detectaram antes da publicação dois pontos de integração corrigidos:
o reconhecimento de `bulk_action` no despachante da ponte, e as duas listas
de revisões aceitas no predeploy, necessárias para reiniciar após 0070.
Os testes antigos de cadeia passaram a incluir a barreira adicional; o teste
de opt-out agora exige a invalidação de Massa além dos dois módulos anteriores.

## Qualificação exigida antes da publicação

- Contratos Python completos e DOM do painel e de seus consumidores.
- PostgreSQL real descartável: migração, RLS, grants, concorrência,
  idempotência, linhas repetidas, cadência, cancelamento, origem e recibos.
- Chrome e WebKit, em celular, tablet e desktop, nos dois temas.
- Comparação das falhas gerais do repositório com a base, distinguindo
  problemas anteriores de regressões da mudança.
- Igualdade entre a árvore qualificada e a árvore a integrar.

O ambiente local não possui PostgreSQL nem os navegadores de teste. Esses
gates são executados pela infraestrutura de CI já existente, com banco e
destinatários sintéticos e saídas externas bloqueadas. Não se usa banco de
produção como banco de teste.

## Verificação após publicação

Ainda pendente: backup privado verificado antes da migração, revisão 0070,
barreiras de leitura antes de liberar API/worker, saúde dos serviços e
conferência do painel conectado. Não confirmar lote real, enviar mensagem,
alterar contatos de produção nem contratar serviço durante essa verificação.

Não se declara equivalência empírica de funções privadas da referência que
não foram observadas. A etapa 10 começa somente após fechar esta publicação
e sua conferência de produção.
