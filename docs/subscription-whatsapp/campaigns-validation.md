# Etapa 07 — evidências de Campanhas

Estado: implementação concluída, em qualificação. Este registro não declara
publicação nem execução de testes PostgreSQL ou de navegador antes de seus
resultados no CI.

## Base preservada

A base inicial era a publicação de Diagnósticos, commit
`81cf127c51c3fae015568d16d79724ebe0f00db8`, árvore
`46d4109b71afa2c986c6580ac994c0113725a17f`. Durante a implementação, a PR257
publicou o transporte compartilhado no commit
`5699297d4b873a411275c767109bab77c1afc5b6`, árvore
`bd4915991953ead3510db7eed4716edfe9cefbd0`.

As 48 alterações publicadas foram incorporadas. Quarenta e sete arquivos ficaram
idênticos à produção; `channel-manager.mjs` preserva suas alterações publicadas
e acrescenta somente o adaptador de arquivos privados de Campanhas. A base e o
índice locais foram alinhados ao commit real 5699297. PRs abertas não integradas
à produção não foram incluídas.

## Revisão e correções antes do CI

- A revisão de audiência conta os telefones distintos antes da materialização;
  a consulta retorna no máximo 10.001 linhas deduplicadas. Uma combinação de
  grupos não carrega centenas de milhares de associações na aplicação.
- Datas próximas ao limite do calendário e janelas que atravessam meia-noite
  produzem validação explícita, incluindo inexistência de próxima janela.
- A lista real de fusos revelou `Factory` e `localtime`, incompatíveis com Intl.
  Essas pseudozonas são recusadas, opções não suportadas pelo navegador são
  filtradas e um fuso já salvo nunca é substituído silenciosamente.
- A expansão de marcadores tem orçamento agregado incremental de 64 MiB,
  verificado tanto na revisão quanto na confirmação, antes de gravar audiência.
- Remoção de contato e revogação invalidam pendências no mesmo bloqueio do tenant.
  Reintroduzir um contato no grupo não reativa uma campanha anterior.
  O marcador persiste também depois do claim e durante pausa/retomada, sem
  modificar o ledger; um recibo tardio conserva a verdade do transporte.
- O detalhe confirmado usa o resumo completo dos grupos congelados, incluindo
  quantidade de bloqueados e estado de arquivamento, compatível com o controller.
- A transcrição já autenticada registra a conclusão do ASR antes de processar
  opt-out do evento de áudio original, tudo na mesma transação. Não há chamada
  adicional ao provedor de transcrição.
- A ponte usa a recuperação de mensagens publicada na PR257: somente ID,
  destinatário e direção corretos recuperam o protocolo cifrado. Um ID sozinho
  é recusado também nos testes novos de Campanhas.

## Evidência local

| Conjunto | Resultado observado | Limite da evidência |
| --- | --- | --- |
| Ponte, gate dedicado sobre 5699297 | 411 aprovados, zero falhas/skips | Sockets e provedores sintéticos; SDK nativo e armazenamento cifrado reais. |
| Permissões, rotas e histórico | 167 aprovados | Campanhas restrita a assinaturas e sem reenvio genérico no histórico. |
| Python, gate dedicado final | 1.743 aprovados, seis avisos de depreciação; 58,79 s | Inclui as regressões finais de orçamento, fuso, revogação após claim e contrato do detalhe. |
| Backend funcional de Campanhas | 137 aprovados | Conjunto incluído no gate Python final. |
| DOM completo com espaçamento do menu | 344 aprovados | Inclui 52 casos de Campanhas; 17,60 s. |
| DOM de Campanhas após alinhamento final da fixture | 52 aprovados | Primeira página do detalhe e três destinatários coerentes com a API; 14,05 s. |
| Smoke HTTP em loopback | 8 aprovados | Schemas reais; sete operações, uma confirmação sintética e nenhum efeito de provedor. |
| Coleta de navegador | 32 casos, 16 por engine | Coleta e compilação; execução de Chrome oficial/WebKit fica no CI. |
| Coleta PostgreSQL funcional | 32 casos | Cookies/CSRF, papéis SQL e broker reais; execução fica no CI. |
| Release local | 311 aprovados | Backup, barreiras, sanitização e compatibilidade, documentados separadamente. |

O banco descartável deve executar os testes funcionais e os 51 testes de catálogo
da release com os três papéis reais. Todos os envios, contatos, recibos, áudios e
objetos de armazenamento usados na qualificação são sintéticos. Os testes de
navegador geram capturas de celular, tablet e desktop, claro/escuro, incluindo
revisão e recuperação após perda da resposta.

## Primeira execução no CI: bloqueios encontrados e corrigidos

A PR258 foi aberta com head `da7280fc334bed8cc26e53c8021839d0662210f2`,
árvore `2c8e4043e88b6bd8d5d32d8caaada61789b72646`. O checkout dos testes foi
`023b94e55d099544776bda2d584271c004b810bd`, merge sintético com a produção
5699297 e a mesma árvore. Essa revisão **não foi qualificada para publicação**.

| Gate dedicado, execução 41927000238 | Resultado final |
| --- | --- |
| Python | 1.743 aprovados, seis avisos |
| Ponte | 411 aprovados, zero falhas/skips |
| DOM | 342 aprovados, zero falhas/skips |
| PostgreSQL | 312 aprovados e 25 falhas; migração e 51 casos de catálogo aprovados |
| Chrome | 60 aprovados e uma falha; execução interrompida no primeiro erro |
| WebKit | 60 aprovados e uma falha; execução interrompida no primeiro erro |

Os 25 traces do PostgreSQL identificam duas consultas: vinte falhas na projeção
de destinatários e cinco no controle de intervalo usado por polls anteriores.
O parâmetro sem tipo em `:now - interval` era inferido como intervalo, impedindo
a comparação com timestamps. Quatro expressões passam a usar
`CAST(:now AS timestamptz)`. Uma regressão funcional adicional prepara essas
consultas com audiência vazia e verifica o poll ordinário e a leitura de rascunho.
Não houve alteração de schema ou de permissões para resolver essa falha.

No Chrome, um debounce de pesquisa ainda pendente invalidava a leitura de
detalhe iniciada por uma ação explícita. Três testes com timer controlado
reproduziram a perda da revisão, antes de qualquer confirmação; o controller
cancela esse timer ao iniciar a leitura ou ação explícita. No WebKit, o mesmo
cenário chegou a uma etapa posterior: revogar acesso enquanto a carga passiva
do detalhe ainda estava em curso removia corretamente a rota antes do clique
do teste. O cenário passa a aguardar o estado observável de carga concluída
antes de revogar, mantendo a resposta 403, a remoção do menu e a ausência de
novas escritas, confirmações e efeitos. Os timeouts não foram aumentados.

A inspeção de 36 capturas do Chrome, em três tamanhos e dois temas, encontrou
abas de destinatários com aparência nativa e palavras quebradas no celular e
tablet. O componente passa a usar as abas existentes do Orion, com quebra
entre botões e rótulos inteiros; o cenário visual mede a quantidade de linhas
de cada rótulo, a altura e a contenção. As novas capturas verificarão o ajuste.
Depois das correções, passaram 137 unitários do backend e 55 testes DOM de
Campanhas. Foram coletados 33 casos funcionais de PostgreSQL e 32 de navegador,
sem declarar essa coleta como execução. A revisão independente dos quatro
casts e do cancelamento do timer não encontrou novo bloqueio.
Os resultados finais da nova árvore devem substituir qualquer conclusão de
prontidão; os erros desta primeira execução ficam preservados como evidência.

## Critério de publicação

Exigir os seis gates dedicados e comparar cada workflow geral executado com a
árvore de produção atual, usando os logs finais, o checkout, a fase e a contagem
das falhas. Falhas históricas permanecem explícitas. Um job herdado não é
declarado aprovado, e uma falha nova não é dispensada por outra suíte passar.

Depois do merge da árvore qualificada, verificar backup e catálogo 0068, startup
da API e do worker, deploy dos três serviços no mesmo commit e o formulário no
painel publicado. Consultas ao vivo não criam grupos, campanhas ou envios reais.
A observação de Mensagens Programadas só começa depois dessa conclusão.
