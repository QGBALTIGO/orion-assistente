# Extrator de contatos de grupos — observação e contrato

## Referência acessível

A página <https://botbot.chat/user/tools/group-contacts> foi aberta em
2026-11-05, depois da publicação e conferência ao vivo da etapa 15.
O módulo estava acessível sem complemento pago. A página apresenta o
dispositivo, o seletor Grupo e o botão Carregar membros. A descrição indica
obter números dos membros de grupos WhatsApp e salvar como contatos.

Foi selecionado um grupo disponível e acionada a leitura de membros. A
página exibiu uma tabela com colunas WhatsApp, Nome e Função e seleção por
checkbox. A opção Selecionar todos estava marcada inicialmente. Desmarcá-la
reduziu a contagem a zero; selecionar uma linha alterou o botão para
Salvar 1 contatos. O botão de salvar permanecia habilitado com seleção vazia,
mas não foi acionado. Os campos Nome e Função estavam vazios na consulta
observada; isso não comprova ausência desses dados em todos os grupos.

A área Salvar contatos em um grupo oferece listas locais existentes e
Novo grupo de contatos. Essa última opção revela Nome do grupo, com limite
DOM de 100 caracteres e sugestão `Grupo: <nome do grupo WhatsApp>`.
Sem grupo WhatsApp selecionado, Carregar membros exibe Escolha um grupo.
A referência preservou a tabela anterior nessa troca; o Orion invalida a
seleção ao trocar a origem para evitar salvar a lista errada.

Não foi acionado Salvar, criado grupo local, importado contato ou alterado
participante real. Nenhum telefone ou nome de membro foi copiado para o
repositório. Depois da inspeção, a página foi recarregada e voltou ao estado
inicial, sem tabela ou seleção; o console retornou `errors=[]`.

## Limites de equivalência observada

Foram exercitados a consulta real, a seleção de todos/subconjunto, o destino
existente/novo e a validação de origem vazia. O resultado do salvamento,
tratamento de duplicados, concorrência, limites de volume e recuperação após
falha de rede da referência não foram observados. As regras abaixo são
decisões explícitas do Orion, verificadas com dados sintéticos; não se
atribuem ao comportamento interno do BotBot.

## Contrato de implementação

- Rota `extrator-contatos`, somente proprietário de empresa ativa no workspace
  de assinaturas, com `group_contacts.manage`, independente de plano comercial.
- Grupos reais e membros são consultados na sessão já conectada do dispositivo
  canônico da empresa. A leitura não inicia sessão, reconecta, envia mensagens
  nem altera participantes.
- Tabela e seleção usam componentes nativos do Orion. Telefones disponíveis
  são selecionados inicialmente; trocar empresa, dispositivo ou grupo descarta
  a seleção anterior. Seleção vazia não pode ser salva.
- O destino é uma lista de contatos local existente ou nova, compartilhada com
  Campanhas, Programadas, Massa e Enviar Mensagem. A gravação é aditiva: membros
  anteriores não selecionados e nomes já existentes são preservados.
- Repetidos são identificados por telefone. Uma associação removida só é
  restaurada quando aquele telefone é selecionado explicitamente. A importação
  não revoga bloqueios ou concede autorização de envio.
- A consulta fornece uma prévia opaca cifrada, com validade de cinco minutos
  e vínculo com empresa, proprietário, dispositivo, geração, revisão e grupo.
  O servidor usa os membros dessa prévia; não aceita uma lista arbitrária
  apresentada como resultado do provedor.
- A gravação e seu resultado são atômicos. A mesma chave recupera o resultado
  após perda de resposta; uma chave reutilizada com dados diferentes é
  rejeitada. Prévia expirada exige nova consulta.
- IDs `@lid` sem número real conhecido não são convertidos em telefone. A
  ausência de número/nome e a deduplicação são informadas; limites explícitos
  impedem truncamento silencioso. Não se usa a store global do canal legado.

O SDK já instalado é a fonte do contrato de transporte. As operações de
leitura também foram conferidas no
[código primário do Baileys](https://github.com/WhiskeySockets/Baileys/blob/master/src/Socket/groups.ts).
A implementação usa a versão fixada no Orion, sem atualização de dependência.

## Critério de qualificação e publicação

Esta etapa altera leitura de transporte e importação local. A qualificação
inclui contratos de API, autorização e contexto, PostgreSQL real com os papéis
existentes, ponte com sockets sintéticos, DOM e Chromium/WebKit. Casos focados
cobrem importação aditiva, repetição/conflito de chave, snapshot adulterado ou
expirado, números indisponíveis, troca de sessão e ausência de efeitos de
envio. Os seis grupos dedicados existentes incluem a nova função; a suíte
geral permanece registrada separadamente.

Após a qualificação, API, worker e ponte devem ser publicados e conferidos
na revisão integrada, seguidos da leitura da tela no Chrome conectado.
Nenhuma importação ou alteração de contatos reais faz parte da conferência
ao vivo. A etapa 17 começa depois desse encerramento.

## Qualificação local da candidata

Em 2026-11-05, a implementação passou em 129 testes Python focados, incluindo
52 novos casos do extrator e as regressões de autorização e Campanhas.
Os 23 cenários PostgreSQL foram coletados, mas ainda aguardam execução no
banco real do CI; coleta não é aprovação.

A rodada final DOM passou 56 casos: 34 do extrator e 22 dos guias que
compartilham a guarda de acesso. Esse total já contém os 34 casos executados
anteriormente. A ponte passou 238 casos de `channel-*` e `device-*`, sendo
202 regressões e 36 novos cenários. Um dos novos casos usa o parser instalado
do Baileys 6.7.24 com nós sintéticos para distinguir número explícito de LID.

O Chromium passou 17 casos, com os 17 equivalentes WebKit reservados ao CI.
Foram conferidos os seis tamanhos/temas, importação existente e nova,
preservação de nomes e membros anteriores, recuperação após perda de
resposta, origem vazia/trocada, prévia expirada e fronteiras de acesso.
As capturas em celular escuro e desktop claro foram inspecionadas visualmente.

A primeira rodada de navegação identificou uma segunda lista interna de
permissões que ainda barrava a consulta. A inclusão de `group_contacts.manage`
foi corrigida nessa lista, com um teste DOM que atravessa `suite.request()`
real. A rodada posterior de 17 casos passou; a falha inicial não foi apagada
nem resolvida enfraquecendo as asserções.

A candidata também contém a correção isolada da montagem do fixture de
Enviar Mensagem, cuja dependência da ordem de importação foi revelada pelo
CI geral anterior. A explicação, prova antes/depois e preservação das guardas
de produção estão em [validação do fixture](direct-api-fixture-validation.md).
Essa correção não altera a API de envio nem reclassifica execuções anteriores.
