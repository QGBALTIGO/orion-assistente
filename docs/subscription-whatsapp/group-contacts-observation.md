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
Os 23 cenários PostgreSQL foram apenas coletados nessa rodada local; sua
execução real posterior está discriminada na qualificação da PR 269 abaixo.

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

### Correção de transição em Mensagem em Massa

A candidata inicial da etapa 16 foi `ac977728cd33804c2e2b411e8de0358fa50ace8a`,
árvore `2d476816716aaf6269d473ed4aa0481ebd280e89`. Antes de publicar, a revisão
de uma falha WebKit anterior identificou um defeito independente comprovável:
depois de Corrigir, o formulário de Massa permitia editar e revisar durante
a consulta das opções, que substitui o DOM ao terminar. A guarda `canEdit`
passou a exigir que essa consulta tenha terminado.

Um teste DOM com resposta deliberadamente pendente falhou antes da correção
e passou depois: campos e revisão ficam bloqueados, não há nova gravação
durante a leitura e a correção mantém a chave original. A suíte DOM de Massa
passou 62 casos; os dois casos Chromium existentes de repetição/correção
passaram sem alteração do E2E. A falha WebKit da PR 268 não tinha trace que
permitisse atribuir conclusivamente o clique perdido a essa janela; ela
continua registrada separadamente. A candidata final da PR 269 contém essa
correção e foi qualificada pelos seis grupos registrados a seguir.

## Publicação da PR 269 e conferência real

A [PR 269](https://github.com/QGBALTIGO/orion-assistente/pull/269) foi integrada
normalmente no merge `5633a2ec596f1f7884b17f6c685f296318697fe9`. A candidata
final `c8c0f463317ae0399ae650107da43046720cf7e8` e o checkout integrado
`0e4a66cb422955068e0a1bebc3ff6a12e4b84fc1` possuem a mesma árvore Git
`e58bbfed8bb86053a6fd9d12b4237ff50e0c6f78`, conferida também localmente.

Os seis grupos do [workflow 42084882875](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42084882875)
passaram na primeira tentativa da candidata final: API 2.820, PostgreSQL 611,
DOM 617, ponte 524, Chromium 165 e WebKit 165, totalizando **4.902 execuções
aprovadas**. Os 23 novos cenários PostgreSQL integram os 611. A seleção de
165 casos por navegador deixou os 165 do outro motor para o job correspondente.
Não houve falhas, erros, skips ou repetição nesses seis jobs; a API teve seis
warnings. Esses resultados pertencem à PR 269, antes do corretivo abaixo.

A primeira candidata foi substituída antes de executar os testes dedicados.
Para liberar a fila, seis workflows gerais dessa candidata antiga foram
cancelados manualmente, preservando o histórico parcial. Nenhuma execução da
candidata final foi cancelada. No corte geral de 19:06:31–19:06:33 UTC, havia
45 sucessos, 11 falhas herdadas comparadas, um timeout de FATIA sem resumo
final e dois jobs ainda ativos. O timeout não comprova a remoção global das
21 falhas de fixture; a prova focada está no registro específico. Não se
declara aprovação de todo o repositório.

API `ecc343a5-9f77-4dc9-bafa-c378c1815937`, worker
`d8a8da49-dcfd-4847-89f3-cfecb18b30b9` e ponte
`5b598e24-7aa7-4fea-8e2b-31bb7b679c29` atingiram SUCCESS na mesma revisão.
O ambiente mostrou os cinco serviços online com 1/1 réplica, sem alertas ou
trabalho pendente. Não houve nova migração; o contrato continua em 0071.

A conferência no Chrome conectado encontrou o menu e abriu a tela, mas a
consulta automática de grupos mostrou indisponibilidade. A página
Dispositivos confirmou que o aparelho cadastrado estava **Desconectado**.
Não houve reconexão, envio ou importação real. O console não apresentou erros.
Não há evidência de falha do transporte com uma sessão conectada.

## Corretivo do estado desconectado

A causa reproduzida é a diferença entre elegibilidade para Campanhas e
capacidade de leitura imediata: a primeira admite aparelhos desconectados,
enquanto a segunda exige sessão conectada. O extrator reutilizava a primeira
indicação e iniciava uma leitura que a ponte corretamente recusava.

O corretivo limita a disponibilidade de consulta ao estado `connected`,
revalida essa condição antes e depois da chamada e apresenta o estado real
na interface. O atalho Dispositivos usa sua própria permissão. O consumidor
também recusa o DTO antigo incoerente com `available=true` e status offline.
Campanhas, o transporte, SQL de produto e permissões não foram alterados.

A importação local de uma prévia válida mantém as mesmas verificações de
proprietário, validade, geração e revisão. Uma desconexão isolada não exige
nova leitura para salvar contatos já revisados; remoção, desativação ou troca
de contexto continuam bloqueando a operação.

O critério de liberação deste ajuste de estado é a validação focada do
produtor/API, DOM integrado e Chromium, seguida da conferência da tela
publicada. O estado offline foi reproduzido com dados sintéticos; a
conferência real final está registrada abaixo.
Os 70 testes Python e 37 DOM passaram. O produtor real, reproduzido com a
função anterior, falhou ao devolver disponibilidade para estado desconectado;
com o predicado novo, seu DTO completo coincide com `options_disconnected`,
consumido pelo teste DOM. Os 19 casos Chromium passaram, incluindo a recusa
do DTO legado, somente GET/options quando offline e navegação pelo CTA.

### Qualificação da árvore integrada da PR 270

A [PR 270](https://github.com/QGBALTIGO/orion-assistente/pull/270) foi integrada
por merge normal em `a438a1416577cde1fb643e9c8a200ca90093e084`, sobre a base
concorrente da [PR 267](https://github.com/QGBALTIGO/orion-assistente/pull/267),
`de9d1b3b3099886a069f0aa2a761cbacd26908b3`. O checkout de teste
`7f8dafd5c656b436f0db252f50ed292ad053e6c5` e o merge publicado têm a mesma
árvore Git `5aaa2ef859a4c621009c2655b0c668f766404b31`, conferida localmente.
A integração preserva as alterações de confiabilidade da base concorrente.

A qualificação focada dessa árvore terminou com os seguintes resultados:

| Grupo | Aprovados | Escopo |
|---|---:|---|
| Python | 276 | Extrator, dispositivos, saúde da conexão, revínculo, canais QR e verificações de publicação relacionadas |
| Node | 279 | Ponte e compatibilidade com a base integrada |
| DOM | 37 | Extrator, contrato real de opções offline, isolamento e navegação permissionada |
| Chromium | 19 | Fluxos do extrator, layouts, estado offline, DTO legado incoerente e atalho Dispositivos |

Essas rodadas qualificam o corretivo integrado; os 70 testes Python iniciais
não são somados novamente. As **4.902 execuções dedicadas pertencem à PR 269**
e não são apresentadas como uma nova execução ampla da PR 270.

Os **29 cenários PostgreSQL foram somente coletados**, sem execução local
real nesta qualificação. O [workflow dedicado 42094143383](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42094143383)
continuava com seis jobs em fila no corte de **2026-11-05 às 19:46:20 UTC**.
No mesmo corte, o CI geral tinha sete sucessos, três falhas comparadas como
herdadas, dois jobs em andamento e 43 em fila. As falhas de histórico de
clientes no PostgreSQL, comunicações de agendamento no PostgreSQL e ponte
completa mantiveram os mesmos casos de teste e causas da referência PR 269.
O resultado amplo, incluindo PostgreSQL e WebKit desta revisão, permanece
registrado separadamente; não se declara aprovação de todo o repositório.

### Publicação do corretivo

API e worker atingiram SUCCESS com o merge
`a438a1416577cde1fb643e9c8a200ca90093e084` em 2026-11-05:

| Serviço | Implantação | Estado e horário UTC |
|---|---|---|
| API | `c1d1cfa0-5767-4078-a48f-c6dc237d8cce` | SUCCESS às 19:37:45.822 |
| Worker | `98646f31-9b50-476f-abf4-fd597f9bcd56` | SUCCESS às 19:36:59.875 |
| Ponte | `e6d6a16c-37c0-4c64-9207-773d4bb76159` | SUCCESS, na revisão `de9d1b3b3099886a069f0aa2a761cbacd26908b3` |

A ponte já publicada tem a mesma árvore `qr-bridge` do merge a438:
`c3091aeece93349d3171df38eacfc54ebb04d339`. Por ausência de mudança nesse
componente, o autodeploy da PR 270 foi marcado SKIPPED; a implantação SUCCESS
acima permaneceu ativa. O ambiente exibiu os cinco serviços online, com
uma réplica de uma, sem trabalho pendente e sem alertas.

### Conferência real final no Chrome

Na nova conferência, o dispositivo já apareceu conectado, sem acionamento
manual de conexão ou revínculo nesta verificação. O extrator listou **três
grupos reais**. A leitura do primeiro grupo apresentou **quatro membros**,
com as colunas WhatsApp, Nome e Função, quatro contatos selecionados,
zero números indisponíveis e zero duplicados.

Desmarcar todos deixou a seleção em zero e o botão de salvar desabilitado.
Selecionar uma linha mudou a contagem para um e habilitou o botão. O botão
Salvar não foi acionado: nenhum contato real foi importado, nenhuma lista
foi gravada e nenhuma mensagem foi enviada.

A ação Atualizar descartou a prévia e a tabela retornou a zero linhas. A
conferência terminou em **2026-11-05 às 19:44:54 UTC**, na rota
`/painel?aba=extrator-contatos`, com `alerts=[]` e sem warnings ou erros no
console. A captura final foi inspecionada no padrão visual nativo do Orion.
Nomes e números privados não foram reproduzidos neste registro.

A consulta anterior do BotBot usou outro grupo, com 77 membros. Portanto,
a amostra real do Orion comprova leitura e seleção no contexto observado;
não comprova igualdade dos dados dos provedores ou funcionamento em todos os
grupos. O salvamento, a importação aditiva e suas regras de recuperação foram
exercitados com dados sintéticos. O retorno de salvamento da referência
continua não observado.

A etapa 16 está encerrada como função publicada e com leitura real validada.
Os limites de observação e a pendência do CI amplo permanecem explícitos;
o encerramento permite iniciar a etapa 17 sem atribuir equivalência integral
às mutações não observadas no BotBot.


## CI amplo do corretivo — corte 2026-11-05 20:00:08 UTC

O workflow `42094143383` terminou na mesma árvore integrada da PR270.
Cinco grupos passaram: 2.838 API, 620 DOM, 575 ponte, 167 Chromium e 167
WebKit. O grupo PostgreSQL parou com um aprovado e uma falha antes da ação: o
novo fixture tentou persistir `status=stopped`, recusado pelo CHECK da migração
0062. Esse é um estado da ponte, normalizado antes de chegar ao banco, e não
um estado permitido de `subscription_devices`.

A correção de fixture preparada com a etapa17 substitui esse parâmetro pelo
estado persistido válido `error`, mantendo todas as asserções de bloqueio da
consulta e de ausência de mutação. Os casos unitários para DTO `stopped`
continuam. Não houve mudança no produto nem relaxamento do CHECK. Os 29
cenários PostgreSQL e a suíte completa ainda precisam da execução real da
próxima candidata; não se apresenta o CI270 como seis grupos aprovados.

O corte terminal do CI geral, consultado às 20:25 UTC, contém 55 jobs de 20
workflows disparados: 42 aprovados, 11 com falha e dois cancelados durante a
preparação do WebKit. Dez jobs repetem as falhas anteriores registradas na
PR269. O FATIA terminou com 168 falhas, 43 erros, 10.070 aprovados e dez
ignorados; sua única nova identidade de falha, comparada à execução completa
da PR266, é o mesmo fixture `stopped` acima. As 21 falhas antigas do fixture de
Enviar Mensagem e duas verificações de release não aparecem nessa execução.
Esses resultados posteriores ao merge não representam aprovação global do
repositório nem uma qualificação prévia dos seis grupos da PR270.
