# Revisão manual do inventário de comunicação — 20/10/2026

A revisão abrange os **1.247 candidatos de saída** do inventário congelado do
documento de comunicação. Os 1.247 IDs foram conferidos por função, chamadas,
guardas e destino; 102 arquivos de fonte e dependência possuem hash validado.
O snapshot atual foi reconciliado com `a85da1c`, mantendo os IDs e o histórico
das revisões. A CI desse runtime concluiu com sucesso; a árvore anterior
`08a5cc0` também qualificou separadamente a correção do provisionamento.
Isso não representa uma auditoria integral de todos os arquivos MarcFlow nem
homologação de provedores.

O [relatório pesquisável](revisao-manual-20261020/relatorio.html) contém todos os
registros e os 13 achados únicos, mantendo as 14 observações dos revisores. Seus
recibos distinguem testes desta execução, referências históricas, CI e Railway.
O JSON bruto e o índice compacto acompanham o HTML e funcionam sem rede.

## Correções

- QR: preservar o recibo de uma operação concluída quando a apresentação excede
  o limite; calcular o orçamento completo antes da confirmação; não afirmar que
  uma reserva anterior continua vigente sem consultar seu estado atual.
- Comércio: exigir proposta de cancelamento válida por 15 minutos antes da
  primeira alteração, mantendo recibos idempotentes de cancelamentos já feitos.
- Meta legado: reservar uma única tentativa antes do transporte; um timeout,
  cancelamento ou encerramento após essa reserva exige reconciliação. Recibos
  assinados podem confirmar a entrega sem reenviar. Validar identidade, janela,
  ingresso mais recente, pausa humana e tamanho Unicode antes da tentativa.
- Painel: estados factuais de campanhas/fila, carregamento da fila, preservação
  de texto personalizado, horário com fuso explícito, proteção contra respostas
  atrasadas entre diálogos e descrição fiel da IA no QR.
- Complemento do contrato de aviso QR: `ORION_NOTIFY_SELF_ON_LINK=0` também
  impede o aviso de conexão dos canais gerenciados retomados, sem desligar sua
  IA. O comportamento anterior permanece quando a opção não foi definida.
- Na compatibilidade do administrador, a central passou a exigir identidade
  real também na tela: dois valores nulos não significam conversa assumida.
  A legenda, botões e submissão preservam o rascunho e o acesso de consulta,
  sem tentar enviar quando falta identidade, atribuição ou envio habilitado.
  A prova isolada reproduz o erro no JS anterior e a correção atual. O backend
  já recusava a ação sem operador; a prova não acionou envio real.
- Adoção do QR: aceitar os esquemas exatos 0028, 0029 e 0030,
  preservando tabelas, registros, preferências, recibos e sequência de ingresso. Registrar e ativar
  continuam operações separadas com backup cifrado, leitura de volta e restauração
  isolada. Envios pendentes ou incertos bloqueiam a ativação, sem limpar a fila.

Complemento operacional: a primeira montagem no Railway resolveu uma versão
mais recente do SDK de armazenamento e foi interrompida pela igualdade de
versões antes da migração. O arquivo `requirements.lock` fixa os 52 pacotes
Python observados na CI aprovada; API, worker e CI aplicam essas restrições e
verificam compatibilidade. Isso fixa o grafo Python, sem prometer identidade
binária da imagem-base ou dos pacotes do sistema.

## Publicação e aceite

A publicação foi autorizada pelo proprietário, **sem campanhas ou mensagens
reais de teste**. O estado efetivamente observado está no recibo do relatório;
um plano ou um commit enviado ao GitHub não comprova deploy.

Recibo observado nesta execução: o código `8369e2e` passou na
[CI completa](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39505942545)
com 4.909 testes de backend e 4.773 no Compose, em grupos sobrepostos,
além de 446 testes QR e 325 de interface. Os 52 pins Python coincidiram
nos dois ambientes. Após aplicação manual pelo proprietário no painel Railway,
API, worker e QR foram publicados nesse commit. A migração 0029 confirmou backup
cifrado, leitura de volta, restauração e ensaio, com contagens anteriores
preservadas. O QR confirmou `verified_existing` para 96 arquivos de autenticação
e aparelho conectado no volume original. Os assets públicos coincidiram com o
commit publicado; os detalhes e identificadores estão no recibo JSON.

**A central humana ainda não foi ativada.** A primeira tentativa de registro parou antes do
backup ou INSERT com `existing_tenant_not_active`. O diagnóstico real posterior,
executado como `app_support` em transação `READ ONLY`, confirmou o cadastro
indicado pelo proprietário: **Barbearia Orion — DEMONSTRAÇÃO FICTÍCIA**, slug
`orion-demo`, ID `82552b25-d0c8-5338-8c42-49f11de59e45`. Ele coincide com o QR
configurado e está ativo, mas tem `bot_enabled=false`, zero proprietários
pessoais `member` e nenhum alias legado registrado. Esses estados foram
preservados no diagnóstico. A ativação aprovada posterior está registrada abaixo.
A flag global `QR_AI_ENABLED=1` não ativa a IA de uma empresa cujo
`bot_enabled` esteja falso; não foi realizado teste de conversa real.

Na versão anterior ao provisionamento, o admin legado autenticava como suporte, sem `account_id`.
O usuário definiu que o foco é o próprio administrador. Foi implementada a
[identidade nativa opcional do admin](admin-central-identity.md), mantendo senha
na configuração existente, sessões antigas válidas e sem e-mail fictício ou
outro usuário. A árvore `d11a86b` concluiu sua qualificação em ambiente
isolado e na CI, conforme os [recibos da CI](revisao-manual-20261020/ci-d11a86b-final-evidence.json)
e dos [testes operacionais](revisao-manual-20261020/admin-ops-test-evidence.json).
A [CI principal](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39514675776)
aprovou 5.043 testes de backend (1 ignorado), 4.881 no Compose (163 ignorados),
446 do QR e 329 da interface; os grupos de backend e Compose se sobrepõem.
Também passaram 4 testes de ASR com smoke WAV/OGG e os 10 testes da
[CI de mídia](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39514675733).
Os 52 pins Python tiveram 104 correspondências nos dois ambientes.

Na qualificação dessa árvore, os recibos locais registram 34 casos PostgreSQL e 24 unitários únicos
(9 de provisionamento e 15 do helper de credenciais). Os 3 casos PostgreSQL
direcionados após a correção de fixtures se sobrepõem aos 34 e não ampliam o
total. O PostgreSQL local usou autenticação `trust`; a CI isolada confirmou
credenciais distintas por papel com SCRAM. Esses ensaios não representam
provisionamento ou teste de mensagens em produção.

O proprietário aplicou o patch `8f101287` somente da API. O deploy `61a5b9f9`
publicou `d11a86b` com sucesso e concluiu a migração estrutural 0030, com backup
cifrado de 474.010 bytes, leitura de volta, restauração e ensaio confirmados.
As contagens anteriores foram preservadas. A fase `plan` retornou
`writes_performed=false`, hash `03a5dcc9…` e a sessão QR permaneceu conectada.
Os [recibos reais](revisao-manual-20261020/railway-native-admin-phase1-results.json)
e as [verificações públicas](revisao-manual-20261020/public-after-native-admin-phase1.json)
confirmaram API saudável e três assets iguais ao commit naquela etapa.

O proprietário depois aprovou e aplicou o plano de provisionamento no patch
`8792e593`. A tentativa da API `ee59fb67` foi recusada com
`approved_plan_mismatch` antes do backup e de qualquer escrita no cadastro.
O worker `cf3fa7bd` publicou `d11a86b` com sucesso, validou a configuração
administrativa e a barreira do esquema 0030; o QR permanece em `8369e2e`, com
sessão e volumes preservados. O hook normal da API foi restaurado e as opções
de uso único foram desligadas sem outro redeploy. Os
[recibos dessa tentativa](revisao-manual-20261020/railway-native-admin-setup-attempt1-results.json)
registram a recusa e a ausência de aplicação do estado comercial.

O [diagnóstico isolado de determinismo](revisao-manual-20261020/diagnose_admin_plan_determinism.json)
obteve resultados estáveis com sete sementes de hash e reproduziu invalidação
do plano ao mudar somente quatro campos transitórios de posse do canal, com
contagens e plano de negócio inalterados. A correção do snapshot de
provisionamento para esses campos passou nos 15 casos PostgreSQL e nos 10
unitários do [ensaio isolado](revisao-manual-20261020/admin-lease-test-evidence.json);
esses grupos incluem casos anteriores e não devem ser somados aos totais da CI.
O original 0029, suas 84 tabelas e a sequência de ingresso ficaram intactos,
o clone foi removido após verificar sua identidade e o PostgreSQL próprio foi
parado. A adoção já usa a mesma projeção dos campos transitórios. A qualificação
da árvore de publicação foi concluída; o plano real fresco é uma etapa separada.
Essa regressão isolada não identifica a causa exata da divergência em produção,
pois o snapshot completo original não foi emitido.

Antes da ativação persistente, outro teste isolado reproduziu uma resposta 409
do ingresso adotado com controle global desligado: as leituras iniciais de
autoridade aconteciam antes de ligar o contexto do canal. A correção mínima
liga esse contexto antes das leituras e mantém sua limpeza em `finally`.
Os dois pontos de retomada/handoff em `run_locked` também consultam o controle
efetivo do canal adotado. A rodada final passou em 2.204 testes locais QR/inbox,
incluindo 22 novos casos, e em cinco testes com autenticação, RLS e guardas
transacionais PostgreSQL reais, usando inferência substituída. As provas
[unitárias](revisao-manual-20261020/api-adopted-scope-unit-evidence.json) e
[PostgreSQL](revisao-manual-20261020/admin-scope-test-evidence.json) distinguem
esses ambientes e as reproduções vermelhas anteriores. A
[CI completa de a85da1c](revisao-manual-20261020/ci-a85da1c-final-evidence.json)
aprovou 5.074 testes de backend com PG16/Redis, 4.909 no Compose (grupos
sobrepostos), 446 QR, 329 DOM, 4 ASR com smoke WAV/OGG e 10 de mídia.
Pipcheck e as 104 correspondências dos 52 pins foram confirmados. A central
não foi ativada com os defeitos conhecidos anteriores.

Após essa qualificação, o patch `3c490d9e` preparou dez alterações somente da
API: fonte `a85da1c`, gate 0030 e emissão do plano `plan`, sem DDL ou gravações
de identidade, proprietário e IA. Worker, QR e volumes não fazem parte desse
patch. O proprietário aplicou a etapa e o deployment `a8fe8606` concluiu com
sucesso: `plan_only`, `writes_performed=false`, novo hash `dbc3562c…`, gate 0030
pronto e bridge conectado. O hook normal e as opções de uso único foram limpos
sem novo redeploy. O próprio admin, sua membership e a IA da demonstração ainda
não foram gravados por essa consulta. Os
[detalhes revisados](revisao-manual-20261020/native-admin-lease-readonly-staged.json)
registram preparação e o
[recibo real](revisao-manual-20261020/railway-native-admin-readonly-v2-results.json)
confirma o resultado. A aplicação desse plano fresco foi preparada em oito
alterações somente da API, com o mesmo escopo de estado já autorizado.

O proprietário aplicou o plano fresco em oito alterações da API. O deployment
`d22e7b89` concluiu com sucesso e confirmou identidade do próprio admin,
membership `owner` apenas na demonstração, `bot_enabled=true` desse cadastro
e auditoria. Backup cifrado de 476.701 bytes, leitura de volta, restauração,
ensaio, configuração administrativa, registros anteriores e sessão foram
verificados. O [recibo real do setup](revisao-manual-20261020/railway-native-admin-setup-v2-results.json)
registra essa aplicação explícita; não foi contornada a recusa do plano anterior.
O hook normal foi restaurado, opções de uso único limpas e a opção nativa de
runtime mantida ligada, sem reinício adicional. O admin precisa de novo login
para receber a identidade auditável; a sessão antiga continua válida.

O registro protegido do QR (C) foi preparado em cinco campos somente da API,
com novo UUID, backup/restauração/ensaio próprios e controle/envio globais em
zero. O QR e o worker não fazem parte desse patch; D/E/F continuam pendentes.

Os 4.410 testes unitários locais da preparação antecedem a última guarda do
operador de provisionamento. São evidência daquela árvore de trabalho; a
qualificação da árvore `d11a86b` está nos recibos de PostgreSQL e CI, anteriores
às correções operacionais qualificadas posteriormente em `08a5cc0` e `a85da1c`.

A rodada anterior de CI teve 5.012 aprovações e 16 falhas pela senha do
proprietário reutilizada nas fixtures de runtime/suporte; a correção de
`d11a86b` altera apenas testes e passou nos 15 unitários do novo helper.

A primeira consulta de diagnóstico falhou por incluir uma tabela sem permissão
de leitura para suporte. A consulta foi reduzida sem ampliar permissões e passou
nos três testes com clone PostgreSQL 16 antes de ser aplicada novamente. Os
testes confirmaram bloqueio de escrita e preservação das 84 tabelas do original.
O diagnóstico corrigido foi publicado com sucesso na mesma versão `8369e2e`;
depois, o hook normal foi restaurado sem outro redeploy. Os cinco serviços
seguiram online e não restou patch pendente daquela etapa de diagnóstico. Os
[recibos do diagnóstico](revisao-manual-20261020/railway-readonly-diagnostic-results.json)
e os [testes isolados](revisao-manual-20261020/diagnostic-clone-summary.json)
acompanham o relatório. Flags de migração/adoção, a sessão e os valores globais
anteriores da IA foram preservados.

Depois de resolver o pré-requisito do cadastro, faltam registro protegido,
adapter QR com prova da mesma sessão, adoção persistente sem backlog incerto e
habilitação do controle/envio na API e worker. Meta e campanhas permanecem
desativados. O controle do conector cancelou as confirmações anteriores, exigindo
aplicação manual no painel; isso não foi contornado por outro acesso.

A migração aditiva 0029 da API foi concluída antes do novo worker e do bridge.
O volume QR, diretório de sessão, chaves, allowlist e IA existente devem ser
preservados. A ativação da autoridade central exige registro prévio do alias,
prova da mesma sessão e ausência de backlog incerto. Falha nessas barreiras
impede avançar; não autoriza apagar dados, forçar reconexão ou fazer downgrade.

Modelos externos, WhatsApp nativo, entrega real, mídia completa e executor de
fluxos continuam sujeitos aos critérios próprios de homologação. Os resultados
de infraestrutura descartável e transporte substituído não comprovam esses
aceites. O documento de missão e os relatórios anteriores continuam históricos.
