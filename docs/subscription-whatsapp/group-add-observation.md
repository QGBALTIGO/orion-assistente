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
