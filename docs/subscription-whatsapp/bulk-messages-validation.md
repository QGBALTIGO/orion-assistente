# Etapa 09 — validação de Mensagem em Massa

## Estado

Implementação em qualificação, em 2026-11-05. A etapa 09 ainda não foi
publicada. A retomada está na [PR 262](https://github.com/QGBALTIGO/orion-assistente/pull/262).
A base de produção é
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

A validação completa de PostgreSQL 16, Chrome oficial e WebKit é executada
pela infraestrutura de CI já existente, com banco e
destinatários sintéticos e saídas externas bloqueadas. Não se usa banco de
produção como banco de teste.

### Retomada e correções verificadas

A implementação recuperada foi transferida para uma cópia isolada depois de
comprovar que sua árvore-base era idêntica à produção. A primeira candidata
da retomada é `131dd83531324df1b5eb9c431d013eb2c8e70192`, árvore
`6d70247edc1484f6f97f73c8c3f98602d3d570e9`.

O navegador local de teste encontrou um fechamento ausente no resumo da
revisão. No celular, isso colocava o rodapé de confirmação dentro da área
rolável e fora do viewport. O fechamento foi corrigido; uma regressão DOM
verifica a hierarquia e o fluxo real verifica a geometria. Os 14 cenários
locais Chromium passaram em 390×844, 768×1024 e 1440×900, nos dois temas.
A suíte DOM específica passou 61 casos. Esses resultados não substituem
os gates de Chrome oficial e WebKit.

Os testes funcionais de PostgreSQL agora exercitam os endpoints reais,
autenticação, CSRF, papéis, concorrência, origens, recibos e recuperação.
A coleta local e os testes unitários não são apresentados como prova de
execução no banco. O PostgreSQL local instalado não pode iniciar sob o
mapeamento de usuários disponível; o banco descartável do CI é a referência.

A conferência da publicação identificou uma janela entre as versões da API
e da ponte. O protocolo explícito do poll impede que uma ponte anterior
admita trabalho de Massa ou receba seus descritores. A limpeza e a revogação
de trabalho antigo permanecem ativas. A publicação só se encerra depois
de confirmar os deployments e a interface, sem depender da ordem dos builds.

O CI geral mantém falhas anteriores. O comparativo de transporte da primeira
candidata registrou `new_failures: {}` e `global_suite_passed: false`:
820 testes passaram e os mesmos 19 IDs legados falharam. Campos também
preservou os dois IDs de contratos e seis IDs de DOM já presentes na base.
Os resultados completos da candidata final são vinculados na PR antes da
integração; contagens de execuções sobrepostas não são somadas.

No PostgreSQL da primeira candidata, os 36 cenários funcionais novos e o
contrato de migração passaram. A execução parou depois de 158 aprovações em
um teste anterior de concorrência entre Campanhas e Programadas: a exclusão
mútua passou, mas a asserção final consultava apenas efeitos de Programadas.
A nova ordem justa pode admitir Campanhas primeiro. A regressão passa a
conferir o efeito e o destinatário em execução no conjunto dos módulos,
mantendo a exigência de exatamente um trabalho admitido. A nova candidata
também exercita a compatibilidade entre versões da ponte no banco real.

## Verificação após publicação

Ainda pendente: backup privado verificado antes da migração, revisão 0070,
barreiras de leitura antes de liberar API/worker, saúde dos serviços e
conferência do painel conectado. Não confirmar lote real, enviar mensagem,
alterar contatos de produção nem contratar serviço durante essa verificação.

Não se declara equivalência empírica de funções privadas da referência que
não foram observadas. A etapa 10 começa somente após fechar esta publicação
e sua conferência de produção.

Após 0070, o predeploy da versão anterior aceita no máximo 0069. A recuperação
de uma falha de aplicação deve usar uma correção compatível com o novo esquema;
não executar downgrade destrutivo nem supor que republicar a imagem anterior
reverte a migração. O backup privado mantém o procedimento de recuperação
de dados separado do reinício dos serviços.
