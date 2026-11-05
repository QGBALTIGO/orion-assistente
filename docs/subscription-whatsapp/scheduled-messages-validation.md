# Etapa 08 — validação de Mensagens Programadas

Status em 2026-11-05: implementação em validação. **Ainda não publicada.**
A etapa 07 foi encerrada antes de iniciar a observação desta etapa.

## Referência e limite da observação

Referência aberta na sessão conectada: `https://botbot.chat/user/schedule-message?channel=whatsapp`.
O [registro de observação](scheduled-messages-observation.md) descreve a lista,
abas, filtros e formulário exercitados sem salvar um agendamento nem enviar
mensagens. O [contrato](scheduled-messages-contract.md) distingue o que foi
observado das decisões explícitas do Orion.

Não havia ocorrências na referência para exercitar criação efetiva, histórico,
edição, cancelamento, envio, recorrência ou o alcance do botão global.
Não se afirma equivalência comprovada de comportamentos privados não observados.
Nenhum complemento foi comprado e nenhum acesso pago foi contornado.

## Adaptação implementada

A rota `programadas` usa a navegação, os campos, tabelas, diálogos, tipografia,
temas e componentes existentes do Orion. A permissão `scheduled_messages.manage`
se aplica somente ao workspace `subscription_services` da empresa ativa de
`servico_assinaturas`, com o proprietário autenticado.

O formulário conserva título, um dispositivo, grupos e contatos, seleção total,
data/horário, fuso da empresa, intervalos de 10 a 60 segundos, estimativa,
repetição a cada 30 dias e texto próprio ou um dos oito tipos de template
existentes. A seleção resolve contatos existentes no servidor, elimina
telefones repetidos e preserva o nome e as fontes da audiência revisada.

A revisão não cria uma série nem um efeito de envio. Confirmar usa a mesma
chave de idempotência, a revisão vigente e autorização explícita. Uma resposta
perdida é recuperada por consulta, inclusive após recarregar o painel. Uma
consulta negativa não autoriza inventar uma nova chave para o mesmo pedido.
As requisições de leitura podem ser abortadas ao trocar de contexto; uma
mutação já enviada conserva sua identidade de recuperação.

A execução reutiliza o poll privado do dispositivo. Agendamentos futuros
ficam no banco, sem efeitos de transporte prematuros. Campanhas e Programadas
compartilham a serialização, o espaçamento e o bloqueio por contato. Cada parte
da mensagem exige contexto, fonte e permissão atuais; cancelamento e opt-out
interrompem o trabalho pendente. Um recibo aceito é preservado; resultado
incerto não gera repetição automática nem reenvio pelo histórico genérico.

A recorrência usa a data de origem mais 30 dias por ciclo, no fuso confirmado,
e cria somente uma próxima ocorrência depois que a anterior termina. Não
inclui contatos novos nem reproduz ciclos perdidos. Horários locais ambíguos
ou inexistentes exigem atenção. Pendências de dias anteriores precisam de
liberação explícita; não bloqueiam a seleção de ocorrências devidas de hoje.

## Banco e publicação

Migração: arquivo `0069_subscription_scheduled_messages.py`, revisão curta
`0069_subscription_schedules`, após `0068_subscription_campaigns`.

As quatro tabelas privadas conservam série, ocorrência, destinatários e pedidos.
Chaves compostas vinculam empresa, série, ocorrência, destinatário e efeito.
Edições anteriores à admissão mantêm a revisão anterior dos destinatários
cancelada e inserem a nova, sem apagar o histórico. O runtime e as projeções
selecionam o hash da revisão atual.

O predeploy exige o catálogo anterior íntegro e backup privado verificado com
leitura integral do checksum remoto antes da migração. A API e o worker usam
somente verificações de catálogo em modo de leitura, depois da remoção das
credenciais temporárias. Os contratos de todas as etapas anteriores permanecem
obrigatórios. Nenhuma migração ou teste desta etapa foi executado em produção.

## Evidências locais até este registro

- Permissões, histórico e inicialização: 116 testes passaram na primeira rodada.
- Publicação de Campanhas e Programadas: 64 testes passaram.
- Ampliação da cadeia de inicialização: 142 testes passaram após incluir a nova
  barreira nos mocks, mantendo os bloqueios por falha de cada contrato.
- DOM existente: 57 testes passaram; o extrator do adaptador de API foi
  atualizado para seu quarto argumento opcional. Os dois testes do adaptador
  real passaram, incluindo AbortSignal e prova restrita de versão desatualizada.
- Rodada Python integrada inicial: 2.032 passaram e dois testes antigos de
  opt-out precisaram reconhecer a invalidação adicional de Programadas.
  Após a correção, os 81 testes de runtime de Campanhas e wiring passaram.
- Interface nova: 45 testes DOM passaram na rodada final. Foram preparados 34 casos de Chrome
  e WebKit para telas, temas, recuperação e mudança de contexto.
- Política e API novas: 120 testes passaram na rodada final do responsável.
- Conjunto local do módulo: 285 testes passaram antes da última regressão de
  encerramento externo; runtime de Programadas passou em 98 casos após a correção.
- Ponte focada: 72 testes passaram, incluindo 39 novos de Programadas.
- A revisão final encontrou destinatários que continuavam em execução depois
  de seus efeitos serem encerrados pela alteração/remoção do dispositivo.
  Programadas e Campanhas agora projetam esses efeitos terminais antes do
  bloqueio compartilhado da fila. O cancelamento também reconcilia a ocorrência,
  preservando partes realmente em andamento e recibos já existentes.
  As regressões reais desse caminho estão no gate PostgreSQL descartável.

Estas contagens são rodadas, com sobreposição; não representam uma soma de
casos únicos nem substituem o resultado final da integração contínua.

## Validação necessária para encerrar a etapa

A revisão candidata precisa passar pelo workflow dedicado com PostgreSQL real,
ponte sintética, API, DOM e ambos os navegadores. Os testes de banco exercitam
cookies/CSRF, proprietários, isolamento, concorrência, revisões, mídia privada,
opt-out, tempo, recorrência, recibos e alterações reais dos catálogos e grants.
Depois disso, publicar a mesma árvore testada e conferir API, worker, ponte e
interface ao vivo, sem criar envios reais para validar o visual.

A etapa 09 não foi iniciada.
