# 05 — Mensagens Enviadas: qualificação

## Estado da entrega

Implementação e validação em andamento. Este registro não declara publicação
nem aprovação de PostgreSQL ou de navegador antes dos resultados do CI.
Base de produção: `7164ce5ca9e4c795910ca5255f510f625dad0a11` (Templates,
PR 251), árvore `ef30f3c3a5d0507c2f7dc25619bcb7f2b66d91ec`.

O [registro de observação](sent-messages-observation.md) distingue os
comportamentos vistos no BotBot das decisões independentes de autorização,
idempotência, conteúdo preservado e resultado incerto do Orion. Não houve
reenvio real no BotBot nem uso de destinatários reais para testar o Orion.

## Implementação sob qualificação

- Histórico SQL de tentativas de mensagem, filtros, 30 registros por página,
  desempate determinístico e limite de criação preservado entre páginas.
- Totais por empresa/proprietário, fuso local e métricas de áudio concluído
  separadas de reservas diárias UTC e de durações históricas desconhecidas.
- Nova tentativa individual com snapshot congelado, auditoria e idempotência.
  POST não altera a tentativa original; processamento normal posterior pode
  encerrar uma intenção já expirada que nunca recebeu autorização.
- Reautorização no transporte, ordem da sequência e cooldowns preservados.
  Não recompila regras, executa ASR/HTTP novamente nem repete partes aceitas.
- Novos vínculos de seleção em listas/carrosséis. Carrossel usa os três ativos
  privados preservados; um snapshot incompleto bloqueia reenvio com motivo.
- Motivos da ponte classificados em uma lista explícita e mantidos no ledger
  para reconciliação após reinício. Texto de erro do provedor não é exposto.
  Um código fornecido pelo provedor após relay não pode virar prova de não envio.
- Controller do Orion com descarte de respostas antigas, conteúdo escapado,
  confirmação explícita e recuperação somente por leitura após resposta perdida.
  A identidade da operação pode sobreviver a reload, vinculada à mesma conta e
  empresa, sem persistir mensagem ou contato.

## Evidências locais iniciais

| Grupo | Resultado observado |
|---|---|
| Ponte afetada e consumidores compartilhados | 314 aprovados; zero falhas, cancelamentos ou skips |
| Releases 0060–0065 e barreiras encadeadas | 117 aprovados |
| Permissões, classificação de rotas e workspace | 89 aprovados |
| Backend focado de histórico/dispositivos/broker/áudio | 161 aprovados |
| Gate conjunto Python do workflow específico | 1.262 aprovados, zero falhas/skips em 57,94 s |
| DOM de módulos de assinaturas, shell e sessão | 180 aprovados na regressão inicial |
| DOM próprio de Mensagens Enviadas | 24 aprovados após os ajustes finais de áudio, filtro de contato e retorno de foco |
| Gate conjunto DOM do workflow específico | 250 aprovados; zero falhas/skips em 8,60 s |
| Coleta local PostgreSQL e navegador novos | 55 casos coletados; execução real reservada ao CI |

Esses grupos se sobrepõem; os números não devem ser somados como uma única
suíte. Os gates conjuntos locais do candidato estão aprovados; os grupos PostgreSQL
e navegador ainda exigem execução no CI.
O ambiente local não tem PostgreSQL/Redis descartáveis nem executáveis dos
navegadores; esses grupos serão executados no CI isolado, com Chrome oficial
para os codecs da aplicação e WebKit.

## Primeira execução do CI

O [run 41877434304](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41877434304)
executou o merge de teste `1aec349b1715100a90bcbd068eebc0b7d25f3fc0`, cuja
árvore `134c3b7ac283a4135af025ebf2ddad26ba97ddc0` coincide com a implementação
local. Os logs finais confirmaram 1.262 testes Python, 314 da ponte e 250 de
DOM aprovados, sem falhas nesses três grupos.

Chrome e WebKit chegaram a 21 aprovações e pararam no primeiro cenário novo
de celular: o teste tentava fechar a barra lateral pelo botão do cabeçalho,
coberto pelo drawer aberto. A correção usa o controle visível “Recolher menu”
dentro da barra e mantém a asserção de fechamento. Não altera o produto nem
reduz a cobertura; os cenários restantes e a correção exigem nova execução.
Os resultados finais da revisão qualificada serão registrados na PR antes
do merge; esta primeira execução não é evidência de aprovação dos navegadores.

PostgreSQL concluiu 185 aprovações e uma falha na expectativa de um teste:
`app_runtime` não tem SELECT no ledger privado de efeitos, enquanto a fixture
esperava receber zero linhas. A expectativa foi corrigida para exigir a
negação por privilégio, sem ampliar grants. Os demais contratos de migração,
RLS, concorrência e reenvio passaram nessa execução.

A revisão dos cenários restantes também identificou que o shell inicial,
antes de carregar a empresa, apagava o estado de recuperação da resposta
perdida. O ajuste preserva somente os identificadores validados e mantém a
checagem de conta/empresa antes de qualquer consulta. Um teste DOM reproduziu
a falha antes da correção e passou depois, incluindo contextos divergentes,
remoção de campos extras e ausência de POST. O gate DOM completo passou com
251 testes após o ajuste; a execução E2E continua exigida.

## Banco e publicação

Migração aditiva `0065_subscription_sent_messages` sobre 0064:

- Auditoria privada `subscription_sent_message_resends`, sem UPDATE/DELETE
  pela role support e sem leitura de conteúdo pela role runtime.
- Unicidade da chave e da origem por empresa, FK composta de tenant e RLS
  forçada; uma tentativa não recebe dois reenvios concorrentes.
- Duração concluída nullable e timestamp de conclusão do ASR, sem backfill
  inventado. CHECK exige valor positivo, duração limitada e estado aceito.
- Índices de histórico por empresa/data, dispositivo e telefone.

O predeploy mantém as verificações anteriores, exige backup privado verificado
antes do upgrade e valida o catálogo real após a migração. API/worker aguardam
a barreira 0065 usando apenas catálogos sob a role restrita. A migração não
cria configuração, contato, mensagem nem chamada a provedor.

## Base de comparação das suítes gerais

Os runs da árvore de produção já possuem falhas históricas; o objetivo desta
etapa inclui demonstrar ausência de novas identidades de falha nas suítes
afetadas, sem ocultar o passivo anterior.

Na base, o [workflow geral](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41861913761)
registrou **7.783 aprovados, 170 falhas, 43 erros e 9 skips** no pytest; a ponte
completa registrou 591 aprovados e 49 falhas anteriores. O
[workflow de campos](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41861913641)
registrou 29 falhas no Chromium e 52 no WebKit, além das falhas históricas de
DOM e contratos. A comparação desta etapa usa os nomes completos dos testes,
sem confundir aumento do total de casos com resolução de falhas.

Os 24 workflows da base foram consultados novamente após atualização dos
metadados do GitHub. Foram preservados os resultados e IDs de falhas de todos
os grupos com conclusão failure, incluindo planos, conexão, cadastro,
comunicações, acesso profissional e links. Falha de instalação/coleta deve ser
discriminada de uma asserção funcional; não atribuir causa sem o log.

## Pendências para encerramento

1. PostgreSQL real, Chrome oficial e WebKit no CI da árvore candidata.
2. Comparação dos workflows gerais com a mesma base de produção.
3. Merge da árvore qualificada e deployments de API, worker e ponte.
4. Conferência da tela publicada no Chrome conectado, sem envio real.

Diagnósticos (etapa 06) só começa depois da última confirmação.
