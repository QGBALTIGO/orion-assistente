# Adicionar contatos ao grupo — etapa 17

## Referência observada

Em 2026-11-05, depois do encerramento da etapa 16, a página autenticada
<https://botbot.chat/user/tools/group-add> foi aberta no Chrome conectado.
O acesso não exigiu complemento pago. Nenhuma adição de participante, convite,
mensagem, compra ou reconexão foi acionada.

A página permite adicionar contatos salvos ou membros de outro grupo a um
grupo do WhatsApp em que o número conectado é administrador. Havia um
dispositivo apresentado e três opções de grupo de destino. Nomes de grupos,
nomes de pessoas, identificadores e telefones reais não são reproduzidos aqui.

| Elemento | Comportamento observado |
|---|---|
| Grupo de destino | Seletor com opção inicial vazia e grupos do dispositivo. A seleção de um grupo sem administração apresenta aviso e bloqueia a adição. |
| Adicionar de | Alterna entre contatos salvos e outro grupo do WhatsApp. Trocar a origem limpa a tabela anterior. |
| Contatos salvos | Filtros por grupo local (todos, sem grupo ou um grupo específico), nome ou telefone, data inicial e final de criação. Datas usam controles de calendário; não havia atributos mínimos/máximos/obrigatórios. |
| Carregar contatos | Pode carregar a audiência sem destino selecionado. Na consulta sem filtros, foram exibidas 1.024 linhas inicialmente selecionadas. |
| Grupo de origem | Seletor de outro grupo e ação Carregar membros. Sem origem/destino, a validação apresentada pede um grupo diferente do destino. |
| Membros carregados | Uma consulta apresentou 77 linhas: 76 selecionadas e uma marcada como já pertencente ao destino, com caixa desabilitada. |
| Tabela | Colunas de seleção, WhatsApp, Nome e Status; controle Selecionar todos fora da tabela. |
| Seleção vazia | Desmarcar todos zerou a seleção e desabilitou a ação de adicionar. A quantidade no botão acompanha a seleção. |
| Troca de destino | A tabela permanece visível. O estado de administração e a elegibilidade são atualizados; não se deve reutilizar a autorização de uma prévia anterior. |
| Filtro sem resultados | Uma busca por texto sintético sem correspondência retornou zero linhas, mensagem de ausência de contatos e ação desabilitada. |
| Cadência | A explicação da página informa adição individual, com três segundos de intervalo, e pede que a página permaneça aberta. Não houve medição de um lote real. |
| Privacidade | A página explica que um resultado de não adição pode decorrer da configuração de aceitar apenas convites. Nenhum envio automático de convite foi observado ou solicitado. |

O teste de seleção individual foi feito em um destino sem administração; a
contagem mudou, mas a ação permaneceu bloqueada pelo contexto. Isso não é
evidência de defeito no controle de seleção. Ao terminar, a página foi
recarregada: formulário inicial e nenhuma tabela de contatos.

## Limites da comparação

O botão que altera participantes não foi acionado. Os retornos de sucesso,
recusa, falha de rede, perda de resposta, reinício da página e concorrência da
referência não foram observados. A descrição de três segundos é evidência
textual da interface, não uma execução cronometrada.

No Orion, a operação usa recibos e processamento duráveis para preservar a
intenção confirmada quando a aba fecha. Essa é uma decisão de implementação
do Orion; não é uma alegação sobre mecanismos internos do BotBot. Perder uma
resposta não autoriza repetir automaticamente uma alteração de participantes.

## Contrato do Orion

O módulo usa a rota `adicionar-ao-grupo` e a permissão `group_add.manage`,
somente para o proprietário de uma empresa ativa em `subscription_services`.
O plano comercial não substitui o nicho, a propriedade ou o estado ativo da
empresa. O visual usa os componentes já existentes no Orion.

A prévia vincula a empresa, a conta, o dispositivo, sua geração/revisão, a
origem, os filtros e o destino. A confirmação seleciona somente telefones
elegíveis dessa prévia. Cada participante tem um resultado próprio e a
cadência é de três segundos. A administração deve ser comprovada pelo
participante da sessão atual, não apenas pelo criador histórico do grupo.

Telefones não são derivados de identificadores LID. Contatos já presentes
não geram nova adição; bloqueios locais são preservados. Perda de autoridade,
mudança de contexto ou resultado desconhecido interrompem as operações
restantes. Recusas individuais comprovadas podem avançar para o próximo
contato, respeitando o intervalo. Não há convite, mensagem ou reconexão
automática como substituto de uma adição recusada.

Um resultado desconhecido sem prova de que a chamada terminou bloqueia a
operação do dispositivo. Depois do término comprovado, outros contatos podem
seguir em uma nova seleção; o mesmo telefone no mesmo destino permanece
bloqueado enquanto o resultado for incerto. Uma consulta posterior que
comprove sua presença pode registrá-lo como já pertencente ao grupo, sem
atribuir a inclusão à tentativa anterior. Ausência, passagem do tempo e perda
da conexão não autorizam repetir a adição.

Antes da chamada ao provedor, a intenção é gravada e a autorização é
revalidada. O intervalo mínimo é preservado inclusive quando a gravação é
lenta. O término de um temporizador não é prova de que a operação terminou.

A origem em outro grupo representa a seleção de membros confirmada na
admissão do lote; ela não é consultada novamente para cada participante.
Para contatos locais, suas referências e versões são revalidadas antes da
execução. Revogação local de autorização, resultados incertos anteriores e
contexto do proprietário/dispositivo são conferidos nas duas origens.

Os filtros de criação usam o timestamp real do cadastro. A migração 0072
acrescenta exclusivamente a leitura da coluna `customers.created_at` ao
conjunto de colunas já autorizado para esse catálogo. As políticas de nicho
e RLS permanecem; não se concede leitura da tabela inteira, escrita ou
delegação. As barreiras de catálogo verificam essa extensão e sua ausência
em esquemas anteriores.

Limites de quantidade e tamanho são limites explícitos do Orion. Uma
resposta acima desses limites deve falhar de forma clara, sem truncar a
audiência silenciosamente. Os exemplos sintéticos compartilhados entre
produtor, interface e testes ficam em
`tests/fixtures/subscription_group_add_contract.json`.

## Validação e publicação

Implementação em andamento. A publicação depende da qualificação integrada
de API/execução, PostgreSQL real/migração, interface, ponte, Chromium e WebKit.
O CI geral será registrado separadamente, com as falhas anteriores e os
limites de execuções incompletas. A conferência em produção deve usar somente
leituras e preparação visual; as alterações de participantes serão
exercitadas com dados sintéticos.

### Revisões da candidata antes da publicação

A primeira configuração de CI não incluía os 41 casos DOM novos; a segunda
candidata corrigiu a lista e a publicação das capturas. Na segunda rodada,
API (3.007), DOM (661) e ponte (637) passaram. PostgreSQL passou os 48 casos
de release e encontrou a falta de leitura de `customers.created_at` no
primeiro caso funcional. Isso foi tratado como falha da candidata, não como
aprovação da suíte ou falha herdada.

Durante a qualificação, a PR271 entrou na produção com melhorias de
preservação da sessão. Sua implementação foi integrada integralmente.
O fixture dos cinco casos de sessão real da etapa 17 ainda apresentava
credenciais incompletas: falhou antes da correção e passou ao usar a
inicialização de credenciais do SDK, mantendo as guardas do produto. A
composição integrada de transporte passou 446 casos localmente.

A candidata seguinte reúne a integração, a leitura estreita do timestamp e
os testes de privilégio correspondentes. O gate PostgreSQL passa a coletar
todas as falhas na mesma execução, mantendo código de saída não zero para
qualquer falha e o bloqueio de saída para provedores. As rodadas anteriores
continuam registradas separadamente; a publicação exige os seis gates
completos da árvore integrada.

A terceira candidata foi qualificada no workflow
[`42107534723`](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42107534723),
na árvore `faa27e7f30d36098526d5c7d33d09db5c2939deb`. API (3.023), DOM (661),
ponte (707) e Chromium (188) passaram. O PostgreSQL completou 686 casos e
falhou em 15: quatorze por inferência inconsistente do parâmetro de estado
na atualização do lote e um pela inferência do parâmetro de tempo na
recuperação de execução interrompida. As consultas recebem tipos explícitos
na correção seguinte; estados, intervalo de três segundos e corte de 45
segundos permanecem iguais. Esses resultados não qualificam a publicação.

O WebKit dessa rodada concluiu 142 casos e falhou no teste anterior de
liberação e cancelamento de Mensagens Programadas. A falha foi registrada
como erro de acesso durante a troca de sessão de uma consulta de ocorrências;
os 21 casos novos da etapa 17 haviam passado. O relatório da rodada e seus
artefatos são preservados, e a candidata seguinte deve concluir todos os
188 casos do navegador.

Uma reprodução controlada em Chromium suspendeu a resposta HTTP de
ocorrências: o diálogo já havia fechado, mas a consulta ainda estava
pendente e a navegação seguinte a abortou. Aguardar a resposta 200 e a
paginação aplicada eliminou essa interrupção, preservando a coleta integral
de erros e o bloqueio de destinos externos. A mensagem específica do WebKit
não foi reproduzida localmente; a correção de sincronização do teste ainda
depende da nova rodada completa nesse motor. Nenhuma configuração de CORS
ou tratamento de erro do produto foi alterado.

A quarta candidata, no workflow
[`42111981290`](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42111981290),
encontrou outra condição de sincronização em um teste DOM anterior de
Programadas: 660 casos passaram e o teste de leitura tardia após desmontagem
falhou antes de iniciar sua requisição. Produto, fixture e esse teste eram
idênticos aos da candidata anterior. Uma reprodução com o cálculo de
identidade suspenso comprovou que uma quantidade fixa de ciclos não garante
o início do GET. O teste agora espera o início efetivo da consulta antes de
desmontar, mantendo as asserções de sinal abortado, resposta tardia, ausência
de conteúdo privado e número de chamadas. Os 45 casos DOM de Programadas
passaram localmente. Essa correção de teste não modifica as consultas SQL
da candidata 4; a candidata seguinte deverá executar os seis gates completos.

A quinta candidata, no workflow
[`42112896390`](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42112896390),
aprovou API (3.023), DOM (661), ponte (707), Chromium (188) e PostgreSQL real
(701), na árvore `9b7e10a73796015adbee4c97667a56a536d08d2d`. A execução de
banco incluiu os 84 casos da etapa 17, resolvendo as 15 falhas SQL nessa
revisão. O WebKit permaneceu instalando dependências, sem iniciar os testes.

O log anterior de instalação do mesmo job mostrou fontes do Ubuntu sendo
baixadas do espelho Azure a cerca de 39 KB/s, em um conjunto de 114 MB. A
quinta rodada já consumira cerca de 14 minutos da janela total de 20 minutos
apenas no preparo do WebKit. A sexta candidata aplica somente a esse motor a
mesma normalização para `https://archive.ubuntu.com/ubuntu` já presente nos
gates API e PostgreSQL. Suites, componentes, chaves, versões, comandos de
teste, matriz e limites são preservados. O produto é idêntico ao da quinta
candidata; sua qualificação será registrada em uma rodada própria.

O FATIA geral da quinta rodada atingiu seu limite de 20 minutos no passo de
testes, com último progresso de 12% e sem resumo final. Esse resultado é
incompleto; a ausência de IDs finais não comprova aprovação nem que o
impedimento seja anterior à mudança. Ele permanece separado dos 701 testes
PostgreSQL efetivamente aprovados no gate dedicado.

A comparação de timestamps mostrou progresso durante essa execução geral,
sem uma única parada longa identificada: a última linha saiu cerca de 38
segundos antes do encerramento. A mesma faixa inicial levou 899 segundos
na terceira rodada e 1.175 na quinta. Isso documenta lentidão nessa faixa;
os marcadores anônimos do modo quiet não identificam isoladamente um teste
ou provam o resultado dos casos que ficaram sem resumo.
