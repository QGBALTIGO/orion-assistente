# 02 — Chatbot: observação, contrato e limites

## Proveniência e escopo

Observação em 2026-11-04 no Chrome conectado do usuário, por navegação e
interação com o DOM visível. Método de estados/ações/evidências do REA descrito
no README. Não foram criadas, editadas, ativadas, excluídas ou restauradas
regras reais; nenhum envio foi acionado. Alterações de formulário foram
descartadas. Identificadores, contatos, textos de clientes e URLs privadas da
referência não são fixtures nem dados a importar para o Orion.

Fonte inicial: <https://botbot.chat/user/chatbot?channel=whatsapp>.
Os caminhos abaixo usam `{rule}` e `{backup}` no lugar dos IDs da conta.

A etapa 01 já está publicada via PR 248, produção
`976fa7a095b053e5589bab52eb058187ebba858c`, árvore
`2fff2bd3529de0eb89892e910701cc77a4b3b0f6`. A etapa 02 só será publicada após
validar sua própria implementação. A interface dos módulos 03 e 04 permanece
fora desta etapa; as dependências internas de templates, menus e mídia fazem
parte do motor de respostas quando necessárias.

## Estados e ações observados

| ID local | Ação | Observação |
|---|---|---|
| chatbot-list | Abrir Chatbot | Contadores de respostas totais, ativas e inativas; ações Exportar, Backups, Importar, Excluir Todos e Criar Resposta. |
| chatbot-filters | Abrir Pesquisa Avançada | Busca por palavra-chave/resposta/ambas, dispositivo, palavra-chave selecionável, estado, modo de correspondência, tipo de resposta, datas de criação de/até. |
| chatbot-expand | Expandir um card | Dispositivo/escopo, correspondência, Editar/Copiar/Excluir, dispositivo e telefone de teste, Testar Resposta, link Testar Chatbot, prévia formatada ou texto bruto; quantidade de mensagens e atraso total. |
| chatbot-edit | Abrir Editar | Painel lateral com a mesma estrutura de Criar Resposta, preenchido com a definição. Nenhuma alteração salva. |
| chatbot-create | Criar Resposta | Painel lateral; rascunho não salvo; fechar solicita Fechar sem salvar ou Continuar editando. |
| chatbot-types | Alternar os oito tipos | Campos condicionais descritos abaixo; nenhuma chamada de URL nem envio. |
| chatbot-sequence | Adicionar resposta | Ordem, mover para cima/baixo, remover e atrasos por item. Limites visíveis abaixo. |
| chatbot-menu | Selecionar Lista de Opções | Título, descrição, rodapé, botão; seções e opções com ação e atraso próprios. |
| chatbot-export | Abrir Exportar Respostas | Tabela filtrável com checkboxes; selecionar tudo habilita Exportar Selecionados. A tentativa de exportação não retornou confirmação pela conexão do navegador; nenhum arquivo foi inspecionado. |
| chatbot-import | Abrir Importar Respostas | JSON/ZIP até 10 MB, dispositivo opcional e Fazer Upload e Visualizar. Nenhum arquivo enviado; tela após upload ainda não observada. |
| chatbot-backups | Abrir Backups de Respostas | Histórico paginado de snapshots diários, data/hora, fonte, número de respostas/linhas de lista, tamanho, estado, Baixar e Restaurar. Aviso de acesso mesmo após expiração da assinatura. |
| chatbot-restore | Abrir Restaurar e visualizar | Três modos, prévia obrigatória com criar/atualizar/excluir/pular/clones de templates/cópias de mídia, ação posterior para colocar na fila. Nada aplicado. |
| chatbot-restore-exact | Escolher Snapshot exato e visualizar | Exige digitar RESTORE; informa backup de segurança antes da substituição; prévia mostra exclusões e criações. Nada aplicado. |
| chatbot-delete-all | Abrir Excluir Todos | Página avisa exclusão das respostas do canal incluindo escopo Todos os Dispositivos, sem recuperação pela própria ação; opção inicial Não e Continuar. Nada excluído. |

## Listagem

Os cards mostram modo de correspondência, palavra-chave, dispositivo ou
Todos os Dispositivos, editar, estado ativo/inativo e expansão. A expansão
não é um menu de contexto: contém os detalhes e ações do registro.

O filtro de tipo da listagem mostra texto, URL, template, lista e botões.
**Botões de Resposta não aparece como opção de criação atual**; não se deve
inventar um editor separado somente a partir do filtro. A lista da referência
tem regras com palavra repetida; a preferência documentada é da regra
cadastrada mais recentemente, não da que foi editada por último.

O envio de teste usa dispositivo e telefone com código do país; deve exigir
uma ação explícita. Abrir, expandir, pré-visualizar ou editar nunca envia.
A seleção do módulo 03 é um link separado, não o próprio envio de teste.
Copiar e Excluir foram identificados, mas não executados no sistema real.

## Definição da regra

### Dispositivo e correspondência

- Escopo Todos os Dispositivos ou um dispositivo específico. A referência
  inclui todos os canais no escopo global; esta entrega do Orion é WhatsApp.
- `equal`: Frase Exata; texto completo.
- `like`: Contém a Palavra.
- `array`: Lista; termos separados por vírgula, qualquer termo pode acionar.
- Acentos são normalizados: ação/acao equivalem. Emojis são permitidos.
- Palavra-chave obrigatória, até 255 caracteres no formulário.
- Havendo a mesma palavra em mais de uma resposta, só uma é enviada e a
  cadastrada por último tem preferência.
- Comando do aparelho do bot: `!palavra-chave` faz o bot responder naquele chat
  como se o cliente tivesse acionado a regra. A distinção entre mensagem
  humana do aparelho e mensagem emitida pelo bot é obrigação da integração.
- A ajuda menciona Saudação por dispositivo, mas a opção não está disponível
  no seletor observado; não inventar um novo modo.

| Palavra especial | Repetição descrita |
|---|---|
| `*` | Uma vez a cada 24 horas |
| `**` | Uma vez a cada 14 dias |
| `***` | Uma vez por dia |
| `****` | Todas as vezes |
| `*****` | Uma vez por hora |

Precedência entre wildcard e correspondência específica, regras de caixa,
espaçamento, fronteira de palavra, ancoragem da janela e comportamento após
falha não puderam ser exercitados em mensagens reais. As decisões do Orion
devem ser explícitas e testadas, sem atribuí-las ao algoritmo privado da fonte.
O texto específico de `/start` para Telegram não é evidência para WhatsApp.

### Texto e Modelos

Sequência iniciada com uma resposta, botão Adicionar Resposta, mover acima/
abaixo e remover. A ordem das ações importa. Atraso antes de cada resposta
entre 1 e 15 segundos, padrão 1. A ajuda limita a soma a 30 segundos e avisa
que itens além desse limite não serão enviados. O Orion deve impedir uma
configuração que silenciosamente descarte mensagens; atraso de transporte
ou limitação de capacidade deve ser distinguido do atraso configurado.

| Tipo | Campos/efeito observado |
|---|---|
| Texto | Campo multiline de resposta. |
| Template | Selecione o template; depende da biblioteca de templates. |
| Figurinha | Selecione o template; ajuda de compatibilidade V3 anexo, V4/V5 figurinha nativa. |
| Recado de Vídeo | Selecione o template; ajuda V3/V4 vídeo comum, V5 recado nativo. |
| Carrossel | Corpo principal e três cards; detalhes abaixo. |
| URL, Servidor Externo ou Webhook | URL, POST/GET, resposta customizada opcional, Ler Tags da URL, Enviar como arquivo. |
| Enviar outra resposta | Selecionar uma regra existente; mostra escopo e inclui regras inativas no seletor. |
| Enviar uma lista de opções | Selecionar lista existente; nenhuma lista cadastrada na conta observada. |

Os nomes V3/V4/V5 são versões da plataforma de referência. Não correspondem
automaticamente às versões da biblioteca Baileys usada pelo Orion.
O seletor de template não tinha opções disponíveis na inspeção. A resolução
de referências a regras inativas, ciclos e dependências removidas não foi
exercitada na referência; o Orion deve validar esses casos de forma segura.

Tags de contato/remetente observadas: `{name}`, `{phone_number}`, `{my_name}`,
`{my_email}`, `{my_contact_number}`. O modal explica cada uma. Não permitir
execução de expressões a partir de tags nem expansão recursiva de valores.

### URL

Método padrão POST, alternativa GET. A documentação mostra um JSON de POST:
`appName`, `messageDateTime` (epoch em segundos), `devicePhone`, `deviceName`,
`senderName`, `senderMessage`, `senderPhone`, `userAgent`. A resposta esperada
contém `reply`. O template customizado permite campos como `{{username}}` e
`{{payUrl}}`; em branco usa o texto devolvido pelo servidor. Enviar como
arquivo transforma a resposta em anexo `.txt`. Ler Tags da URL é uma ação
explícita. Não foi acionada em URLs reais.

O mapeamento de GET, tipos de JSON, limites/timeout, política de reenvio e
chaves aninhadas não foram observados. O contrato Orion deve ter validação
de URL pública, DNS/IP fixado por tentativa, sem redirecionamento, tamanho e
tempo limitados e resultado persistido antes de gerar os envios. Qualquer
consulta de tags deve usar dados sintéticos e avisar que acessa a URL indicada.

### Carrossel

- Aviso da referência: exige API v5, geralmente exibido no celular.
- Corpo principal obrigatório, máximo 1024.
- Três cards fixos, sem botão de adicionar mais cards observado.
- Cada card: título até 60, corpo até 1024, URL de imagem, texto do botão até 20.
- Corpo do primeiro card aparece obrigatório; os outros, opcionais.
- Nenhum campo de URL de ação do botão foi observado.
- Não houve envio nem confirmação de renderização em um cliente WhatsApp.

### Lista de Opções

- Título obrigatório até 60; descrição obrigatória até 1000.
- Rodapé opcional até 60; texto do botão obrigatório até 20.
- Máximo 10 seções e 10 opções somadas entre todas as seções.
- Adicionar seção começa com duas opções; título da seção até 24.
- Opção: título obrigatório até 24, descrição opcional até 72, um dos oito
  tipos de ação acima, atraso de 1 a 5 segundos, padrão 1.
- Adicionar/remover opções e seções; emojis permitidos.
- Seleções devem usar IDs vinculados ao menu efetivamente enviado, nunca
  executar uma ação livre a partir do texto recebido.

## Exportação, importação, backups e restauração

Exportação usa tabela com seleção individual/todos, palavra-chave/prévia/tipo/
dispositivo/estado e filtros de texto, escopo, estado, correspondência e tipo.
O botão inicia desabilitado com zero selecionados e habilita após a seleção.

Importação aceita JSON ou ZIP contendo JSON, até 10 MB, somente arquivos
exportados pela própria plataforma. Atribuir a Dispositivo é opcional:
o padrão mantém atribuições originais; selecionar um dispositivo substitui
o escopo de todas as respostas importadas. A primeira ação é Upload e
Visualizar. O formato interno do arquivo e o restante da prévia não foram
inspecionados: o formato Orion deve ser versionado e documentado, sem anunciar
compatibilidade de importação BotBot que não foi validada.

Snapshots diários incluem todos os canais da fonte; para este módulo Orion,
o grafo do WhatsApp de assinaturas. Histórico paginado inclui data/hora,
fonte (Agendado), quantidade de respostas/linhas de lista, tamanho, status,
Baixar e Restaurar. A fonte informa disponibilidade mesmo após expiração.

| Modo de restauração | Semântica visível |
|---|---|
| Adicionar ausentes | Preserva respostas atuais e adiciona somente as ausentes. |
| Mesclar e atualizar | Atualiza correspondentes, adiciona ausentes, preserva atuais não relacionadas. |
| Snapshot exato | Substitui o grafo atual; cria antes um backup de segurança. |

Prévia obrigatória mostra contagens Criar, Atualizar, Excluir, Pular,
Template clones e Media copies. Aplicação é colocada em fila. Snapshot exato
exige digitar `RESTORE` e avisa sobre exclusão das regras posteriores.
Uma prévia não aplica alterações. Deve ser vinculada à empresa, dono e
revisão atuais, além de expirar; uma revisão obsoleta precisa nova prévia.
As restaurações devem ser atômicas, remapear referências e preservar o backup
de segurança. Não executar URLs, regras nem envios durante importação/restore.

## Integração Orion e evidência técnica

- Perfil persistido `subscription_services`, nicho `servico_assinaturas`,
  permissão nova exclusiva do Chatbot; outras empresas não recebem o módulo.
- IDs e revisão próprios do Chatbot. Editar regra não invalida transcrição,
  chamadas e webhooks do módulo Dispositivos.
- Entrada textual e transcrição canônica alimentam o mesmo motor, com
  ordenação por contato e deduplicação. Revalidar regra/referências, horário,
  nicho, proprietário e lease antes de cada efeito, inclusive após espera.
- Efeitos persistidos, predecessor aceito antes do próximo, protocolo cifrado
  antes do envio, resultado ambíguo não é reexecutado automaticamente.
- Assets próprios, com empresa, hash, tipo e acesso privado. Não reutilizar
  permissões ou registros de templates de agendamento/conversas antigas.
- Nenhum ID, telefone ou texto privado da referência vira dado de teste.

Pesquisa do SDK oficial pinado Baileys 6.7.24, commit
`e0629940ee2d335b0c0119367fd2a934e0fa3189`: `src/Types/Message.ts`,
`src/Utils/messages.ts`, `src/Socket/messages-send.ts`, `WAProto/WAProto.proto`.
Imagem, sticker, vídeo, `ptv:true` e documento têm tipos explícitos. Lista
nativa exige construção do protobuf + `generateWAMessageFromContent` +
`relayMessage`; passar seções junto com texto ao envio simplificado apenas
produz texto. IDs de seleção estão presentes no protocolo. Carrossel existe
no protocolo, mas a entrega/renderização real ainda não foi comprovada.

## Validação exigida antes de publicação

Regras de correspondência e janelas; transcrição e ordem por contato; CRUD e
conflitos; tenant/nicho/RLS; importação malformada, ZIP bomb e referências;
restauração concorrente, segurança e atomicidade; guarda depois de atraso;
menus/seleções/autoria; mídia real decodificada e transporte nativo; URL
privada/redirect/timeout/resultado ambíguo; fronteiras de quantidade/tamanho;
rascunho, foco, acessibilidade, troca de contexto, tema e responsividade;
regressões do módulo 01 e isolamento dos demais nichos.

Os testes com transporte/provedor sintético não comprovam entrega em uma
conta WhatsApp real. Registrar separadamente quaisquer verificações reais e
limitações de compatibilidade. Não usar uma mensagem de sucesso genérica para
transformar uma dependência indisponível em função aparentemente concluída.
