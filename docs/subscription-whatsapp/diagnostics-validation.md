# Etapa 06 — qualificação de Diagnósticos

## Base, escopo e limites

Base publicada inicial: PR 252, commit `f58730ac8e380bd2c12286c69c1db3873d3cbf0f`,
árvore `28ac47dc292556b77b80fbc80607077265011a41`. O commit local equivalente
é `f7063173988ae8b84883fd42d1e52fdbd4a092ea`; os hashes de commit diferem,
mas a árvore é idêntica. A qualificação compara o conteúdo publicado e o novo
patch, sem tomar a branch `main` antiga como produção.

Durante a qualificação, a PR 254 avançou a produção para
`7d2aa7fc5af1bacb5fc9b973e9dcc9efdeeadb13`, árvore
`47facc617bf47fd08f473b8276a83c5f6275bac0`, com os recursos de Agenda e a
migração `0066_appointment_operations`. Essa passa a ser a base da revisão
integrada. O conteúdo publicado da Agenda é preservado, e Diagnósticos passa
para `0067_subscription_diagnostics`, dependente da 0066 já publicada. A prova
anterior sobre a base PR 252 é histórica, não uma qualificação da integração.

Módulo restrito a `servico_assinaturas`, com permissão `diagnostics.manage`.
A observação, seus limites e o efeito automático inesperado da referência estão
em [diagnostics-observation.md](diagnostics-observation.md). O Orion exige
confirmação explícita para a mensagem de teste. Nenhum envio, probe ou alteração
de configuração real é feito pela qualificação deste patch.

O [contrato técnico](diagnostics-contract.md) e a fixture compartilhada
`tests/fixtures/subscription_diagnostics_contract.json` definem os mesmos DTOs
para Python, DOM e navegadores. PostgreSQL, Chrome oficial e WebKit dependem
dos runners isolados do CI; coleta e compilação locais não substituem execução.

## Evidência local antes da PR

| Grupo | Resultado | Cobertura relevante |
| --- | --- | --- |
| Interface e consumidores compartilhados | 281 testes passaram | 30 novos de Diagnósticos, módulos anteriores, navegação, autorização e contexto da conta. |
| Ponte e consumidores compartilhados | 375 testes passaram | Operação de proxy independente de sessão, DNS público e IP fixado, CONNECT/TLS, headers/tempo limitados, self-send confirmado, revogação e recibos. |
| Conjunto Python afetado | 1.368 testes passaram, 6 avisos de depreciação já existentes | Contratos e consumidores dos módulos anteriores, incluindo os 82 testes funcionais e os guards de publicação abaixo. |
| API funcional de Diagnósticos | 82 testes passaram | Corpo limitado, telefone canônico, leituras sem escrita, DTO seguro, expiração, confirmação, broker, tenant ativo, recuperação e recibos antigos. |
| Publicação 0066 e cadeias anteriores | 141 testes passaram | Backup privado obrigatório, origem revisada, validação de catálogo, credenciais restritas e ordem de inicialização. |
| PostgreSQL e navegadores | 80 casos coletados | 45 PG funcionais, 13 PG de release, 22 browsers (11 por engine); execução pendente no CI. |
| Servidor HTTP sintético | Smoke em loopback concluído | Assets e contratos reais, preparação/confirmação/proxy com resposta perdida e recuperação GET; um efeito e um probe sintéticos. |

O conjunto Python completo foi executado sobre a árvore da primeira revisão em 60,71 s.
Os números de grupos sobrepostos não devem ser somados como casos únicos.
Os testes de ponte usam sockets falsos ou proxy/TLS de loopback com certificado
descartável; não consultam o alvo externo real.

Foi eliminada uma corrida no teste DOM de permissão: a asserção de primeira
consulta passou a aguardar a chamada observável, em vez de presumir que o hash
assíncrono da identidade terminou depois de um número fixo de ciclos do event loop.
Não foi reduzida a asserção nem alterado o comportamento do produto para passá-la.

## Primeira revisão do CI e correções

A PR 253 foi aberta sobre a base publicada, com head
`0a7a2c705d6f3df1ff9b45b02ec2d3348c6a0479` e checkout sintético
`c919fec158582f02107b8cc9feab799fd59ddf8a`; ambos têm a árvore
`b82221ff93022879ba766f6951a3cac1c0c79d6f`. O workflow
`41892887386` concluiu Python (1.368), ponte (375), DOM (281), Chrome (52)
e WebKit (52) sem falhas. PostgreSQL concluiu 240 aprovados e quatro falhas.
Esses resultados pertencem à primeira revisão e não qualificam o head corrigido.

As quatro falhas foram rastreadas antes de modificar suas expectativas:

- Duas eram a mesma inferência ambígua de tipo no helper SQL de dados sintéticos,
  antes de entrar nos handlers. O estado recebe agora cast explícito para
  `varchar(20)` no valor e no `CASE`; as exigências funcionais foram preservadas.
- O cenário de proxy independente completou autorização, recheck, conclusão
  duplicada e recuperação. Seu estado final correto é `disabled`, pois o
  dispositivo sintético foi criado e nunca conectado; o probe não o ativa.
- A empresa pausada é bloqueada primeiro pela guarda normal de plano. O teste
  agora exige esse código, mantendo a exigência de preservar o recibo existente.
  A transação genérica anterior também bloquearia essa preservação. Foi criada
  uma transação exclusiva de `finish`/`receipt`, com exceção somente para o estado
  `paused`, sem conceder rede ou dispensar proprietário, nicho, credencial, canal
  canônico ou vínculo imutável da intenção. Foram acrescentadas 19 regressões
  unitárias; os 182 testes diretamente afetados passaram. O gate Python completo
  foi repetido após a correção: 1.387 aprovados em 65,77 s, com os mesmos seis
  avisos de depreciação existentes.

A inspeção visual dos artefatos encontrou um problema adicional no WebKit:
o corpo flexível do diálogo de confirmação colapsava mesmo com os testes
funcionais aprovados. A base de tamanho agora acompanha o conteúdo e mantém
redução/rolagem apenas quando a altura disponível exige. O teste de navegador
passa a verificar a área útil do corpo e a visibilidade do destinatário e do
texto. Os cartões de status passam a uma coluna abaixo de 1.000 px, após a
captura de tablet evidenciar quebra desnecessária do número da conexão.
Essas correções foram encaminhadas à segunda revisão abaixo.

### Segunda revisão

O head `b4a2e1616d430db500297ca2d280c630853c1123`, árvore
`3ff8fc5fbd57561ec649fc6685110832bb7426f9`, foi executado no checkout
`be3c5eadb548082478c7223ab40318a4e6c43323` pelo workflow `41895285372`.
Passaram Python (1.387), ponte (375), DOM (281), Chrome (52) e WebKit (52).
As novas capturas de WebKit mostram destinatário e mensagem inteiros; o tablet
mostra os cartões de status em uma coluna. A asserção geométrica passou.

PostgreSQL concluiu 243 aprovados e uma falha. Os dois cenários interrompidos
pelo seed e o teste de proxy independente passaram. O recibo da empresa pausada
continuou bloqueado: `_require_canonical` chama `_canonical`, que exige empresa
ativa e devolve `404 Empresa indisponível`. A conclusão ainda não foi aprovada,
e a exigência de `finish`/`receipt` com sucesso e nenhum efeito adicional permanece.
Os helpers genéricos de autorização continuam exigindo empresa ativa. A
transação privada de recibos agora lê o ponteiro e as identidades sob o bloqueio
da empresa e usa o seletor canônico puro compartilhado, sem reparar o ponteiro
nem dispensar a recusa de alias, ambiguidade ou identidade diferente.

A regressão foi reproduzida antes do ajuste com o helper genérico real: dois
testes falharam com o mesmo 404. Depois, passaram os 107 casos unitários de
Diagnósticos e os 35 de conexão canônica (142 no total), incluindo seis casos
novos. O teste PG não foi alterado. A sessão de suporte, a política RLS e os
helpers posteriores também foram revistos: não existe outra exigência de empresa
ativa nesse caminho; `finish` atualiza apenas o resultado da intenção reclamada,
e `receipt` usa apenas SELECT/locks e uma projeção de identidade.
O gate Python completo voltou a passar após este ajuste: 1.393 aprovados em
60,28 s, com os mesmos seis avisos de depreciação.
Esta revisão intermediária não autoriza merge: o próximo head precisa concluir
todos os gates, incluindo o caso PG preservado.

### Terceira revisão e correção de recuperação antes do merge

O head `e59ddbd7c1ae853b8fdc4cb0cf6dd66532c7de37`, árvore
`f6f722aa581132713905107fb599ba7e0497ccf4`, foi executado no checkout
`923633f0fa8ae8022334a8b48c3b6f386db7ed09` pelo workflow `41897526778`.
Os seis gates específicos passaram: Python 1.393, ponte 375, DOM 281,
PostgreSQL 244, Chrome 52 e WebKit 52. O caso PG de recibo da empresa pausada
passou inteiro, sem alterar sua asserção. Os artefatos finais foram conferidos
por SHA-256 e inspecionados em celular, tablet e desktop.

Na suíte geral, o pytest principal concluiu 8.075 aprovados, 170 falhas,
43 erros e nove skips. As 213 ocorrências de falha/erro, em 203 IDs únicos,
coincidem em fase, identidade e multiplicidade com a base publicada; houve
189 aprovações adicionais. A suíte Node geral concluiu 657 aprovados e as mesmas
49 falhas históricas. Esses resultados não significam CI geral inteiramente verde.

Fields Chromium apresentou uma ocorrência adicional em
`test_site_address_slug[chromium-768-1024-dark]`: largura interna 435 px
para área útil de 432 px, um pixel além da tolerância já existente. Os 13
arquivos relevantes ao formulário eram idênticos à revisão anterior, na qual
o mesmo caso passou. Uma única repetição do job, sem alterar código ou teste,
concluiu 169 aprovados e as mesmas 29 falhas históricas. A primeira ocorrência
foi preservada; a causa da variação geométrica permanece não comprovada.

O ASR Docker teve quatro testes unitários/API e build aprovados, mas seu smoke
foi cancelado no limite de 25 minutos. Não houve saída entre a entrada no script
e o cancelamento. Os mesmos arquivos e imagem base passaram na produção e nas
duas revisões anteriores; isso não prova a causa do bloqueio. Não foi solicitada
repetição desse job nesta revisão.

Antes do merge, a revisão da PR identificou um bloqueio funcional na recuperação
de uma intenção rejeitada por versão antiga: o servidor devolvia 409 sem criar
registro, mas a UI conservava a chave; após GET 404, a repetição usava novamente
a mesma versão antiga. O bloqueio foi reproduzido em dois testes DOM, um para
preparação e outro para proxy. Esta revisão não foi publicada.

A correção adiciona uma evidência de rejeição exclusivamente à comparação de
versão sob lock, depois de verificar a ausência da chave e antes de inserir.
Ela vincula motivo, chave, dispositivo, espécie e versão recebida. A interface
só descarta a intenção se todos esses valores coincidirem e a chamada for de
admissão; atualiza então o relatório por GET. Erros genéricos, confirmação,
conflitos posteriores ao dispatch e respostas ambíguas conservam a recuperação.
As revisões de configuração são monotônicas, e a preparação revalida a versão
depois do I/O, impedindo que um POST atrasado com a mesma versão antiga crie uma
intenção após essa rejeição. O contrato detalha os limites desse marcador.

Após o ajuste, os 38 testes DOM de Diagnósticos passaram, incluindo oito novas
regressões; o gate DOM completo concluiu 289 aprovados. O smoke HTTP real em
loopback passou seus seis cenários, sem banco, provedor ou envio externo.
Os três casos unitários de rejeição falharam antes da mudança do backend pela
ausência do marcador; depois passaram os 121 casos de Diagnósticos (14 novos).
O gate Python completo concluiu 1.407 aprovados em 59,95 s, com os mesmos seis
avisos antigos. Foram coletados 51 casos PG funcionais (seis novos), incluindo
preparação em voo, replay de intenção já admitida e conflito após dispatch.
O gate PG passa a ter 250 casos; a coleta não substitui sua execução no CI.
Foram coletados 34 casos de navegador (17 por engine, seis novos em cada);
o gate dedicado passará a 58 por engine, mas sua execução depende do novo CI.
Um teste existente de troca de contexto passou a aguardar o GET
observável de recuperação, em vez de presumir o término do hash assíncrono após
um número fixo de ciclos. Suas exigências de isolamento e limpeza foram mantidas.
Uma revisão independente encontrou ainda uma corrida na atualização passiva:
depois de rejeitar A, sua consulta atrasada poderia executar o cleanup enquanto
o usuário confirmava uma nova intenção B. O contador de leitura descartava o
relatório antigo, mas o `finally` antigo liberava os botões de B. Um teste
determinístico reproduziu esse estado. O cleanup agora exige que a intenção
pendente ainda seja a mesma da mutação. O teste passou mantendo os botões
desabilitados, o evento de cancelamento prevenido, a chave de B e um único POST
de confirmação. O gate DOM final concluiu **290 aprovados** (39 de Diagnósticos)
em 11,06 s; backend, PostgreSQL e browsers permaneceram inalterados neste ajuste.
O próximo head deve concluir novamente todos os gates; nenhuma evidência de
revisão intermediária substitui essa qualificação.

### Integração da atualização publicada da Agenda

O head `133eada3b29f0c7938fd2fe253b476e3c0a1ab8f`, árvore
`4033512c90c4d4d230c147361e469ac95bb5fed7`, recebeu a correção de recuperação
e de cleanup, mas nenhum workflow iniciou. A referência direta de produção
mostrou que a PR 254 havia sido mesclada às 19:52:41 UTC, enquanto a metadata
da PR 253 ainda exibia a base antiga. O conflito foi tratado sobre a árvore
publicada `47facc617bf47fd08f473b8276a83c5f6275bac0`.

O merge preserva as rotas, regras, consumidores, interface e testes da Agenda.
O painel conserva `error.detail`, necessário ao fluxo de serviços extras,
junto da evidência limitada de rejeição de Diagnósticos. A migração publicada
`0066_appointment_operations.py` mantém exatamente o blob
`74520f1a8c27fa03dccd35e02fc2fcc4f2d492cb`. Somente Diagnósticos foi renumerado
para 0067, e o grafo Alembic contém um único head.

O backup de Diagnósticos usa `subscription-diagnostics-0067` e registra a
revisão anterior `0066_appointment_operations`. Os releases anteriores
reconhecem ambas as revisões sem repetir DDL. O predeploy e o startup do worker
exigem a prontidão de Agenda antes de Diagnósticos. A barreira de Diagnósticos
também incorpora o catálogo da Agenda; quatro testes PG adicionais verificam
a recusa quando um desses guards anteriores estiver ausente.

A união dos testes DOM de Agenda e Diagnósticos concluiu 351 aprovações em
12,53 s. A união dos dois gates Python concluiu 1.721 aprovações em 69,71 s:
46 arquivos únicos, flags sintéticas combinadas e rede externa bloqueada.
Houve seis avisos antigos de lifespan e um de TestClient/httpx já exercitado
pelos testes da Agenda. Os 239 testes unitários de release/startup passaram; foram coletados
89 casos PG nos três arquivos diretamente relacionados (51 funcionais de
Diagnósticos, 17 de release de Diagnósticos e 21 de release da Agenda).
A coleta não equivale à execução real. A qualificação remota deve abranger os
seis gates de assinaturas, os cinco de Agenda e todos os workflows gerais
acionados pelo diff contra a nova base, inclusive fresh-devices pela alteração
real no startup do worker.

## Gates obrigatórios do CI

O workflow `subscription-chatbot.yml` inclui os seis grupos anteriores e os
novos caminhos de Diagnósticos, a migração 0067, os scripts de release, os testes
PG, o componente DOM e os fluxos reais de navegador.

- Python: contratos públicos/privados, dispositivos, chatbot, templates,
  histórico, permissões, QR e publicação.
- Ponte: todos os testes afetados `device-*`, `chatbot-*`, `channel-*` e seus
  consumidores compartilhados.
- DOM: assets reais e API sintética, recuperação em reload, troca de conta,
  empresa e nicho, logout, confirmação/cancelamento e escapes de conteúdo.
- PostgreSQL: banco descartável com papéis `app_migrator`, `app_support` e
  `app_runtime`; nenhuma rede externa. Conferir ausência de escritas em GET,
  efeitos únicos sob concorrência, RLS/ACLs e catálogo da 0067, preservando o
  contrato da 0066 de Agenda.
- Chrome oficial e WebKit: três tamanhos, dois temas, seleção/Atualizar sem
  admissão, preview, cancelamento, confirmação, resultados, resposta perdida
  e recuperação; conferir limites horizontais e verticais dos diálogos.

Falhas históricas gerais não são apresentadas como testes aprovados. O baseline
qualificado da etapa 05 contém 24 workflows e 62 jobs (6 específicos e 56 gerais).
A comparação geral preserva fase, ID e multiplicidade; falhas sem ID exigem
comparação de comando, etapa, stack e causa. Repetição por falha de infraestrutura
deve conservar a evidência do primeiro resultado e o mesmo conteúdo testado.

## Publicação e conferência

Status deste registro: implementação em qualificação; merge e publicação ainda
dependem dos gates acima. A PR deve registrar head, checkout de merge sintético,
árvore, resultados finais e comparação completa antes do merge.

A publicação usa a branch `security/prelaunch-hardening-20261030`. É necessário
conferir a mesma revisão nos serviços API, worker e ponte; backup privado
verificado e migração 0066 → 0067; barreira de catálogo pronta; e navegação no
Chrome autorizado. A conferência ao vivo será somente leitura, sem confirmar
mensagem ou iniciar probe real. Campanhas (etapa 07) permanece fora deste patch.
