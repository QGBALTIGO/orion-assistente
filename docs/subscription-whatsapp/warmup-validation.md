# Aquecimento — validação e publicação

## Escopo da revisão

Base de produção: `32a90c0fd003c5fbac70c1bd541bf9ce20171040`, PR 263.
O delta funcional adiciona a autorização do guia, a rota e o artigo nativo
do Orion. Não muda banco, processamento, envio, transcrição, ponte ou
infraestrutura. O [contrato observado](warmup-observation.md) descreve a
metodologia manual e os limites das afirmações da referência.

## Verificações locais concluídas

| Verificação | Resultado | O que comprova |
|---|---|---|
| Novos casos de política de workspace | 9 aprovados | Empresa ativa de assinaturas com/sem plano comercial; empresa pausada/suspensa/cancelada; demais nichos em quatro estados de plano |
| DOM de orientação, revisão final | 14 aprovados | Rota, proprietário, perfil, estado ativo, concessão, revogação, conteúdo, tabela, links e ausência de mutações |
| DOM de workspace e orientação, revisão anterior de conteúdo | 29 aprovados | Integração da navegação existente; não somado como execução da revisão final |
| Chromium local, revisão final | 9 aprovados | Seis combinações de viewport/tema e três limites de acesso |
| Sintaxe e patch | Aprovados | JavaScript parseável, YAML válido e `git diff --check` sem erro |

O browser usa o painel e os assets reais, com servidor de dados sintéticos
em loopback. Os viewports são 390×844, 768×1024 e 1440×900, nos temas claro
e escuro. Foram verificados quatro passos, tabela de contatos, legenda,
qualificação dos exemplos, links oficiais e ausência de overflow.
No celular e no computador, o atalho abre a lista vazia de Dispositivos e
volta ao guia. Os casos de perfil comum e empresa inativa mantêm uma
concessão sintética antiga propositalmente, para verificar o bloqueio
independente do contexto. Não ocorreram pedidos externos, erros de página
ou requisições diferentes de GET. Capturas de celular escuro e computador
claro foram inspecionadas visualmente.

São testes sintéticos e emulação de viewport; não representam aparelhos
físicos, entregas de mensagens nem uma prova de reputação do número.

## Qualificação da revisão integrada

Os seis grupos preexistentes do workflow `subscription-chatbot.yml` foram
preservados. Os novos casos DOM e browser foram incluídos; o unit de
workspace já integrava o grupo de API. Nenhum novo gate de migração,
PostgreSQL ou ponte foi criado para este artigo estático.

Os seis grupos foram aprovados na primeira tentativa da
[execução 42067771635](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635),
concluída em 2026-11-05. Os logs confirmam o checkout da mesma revisão de
integração em todos os grupos.

| Grupo dedicado | Execuções aprovadas | Evidência |
|---|---:|---|
| API, motor, arquivos reais e publicação | 2.768 | [Job 127386625218](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635/job/127386625218) |
| PostgreSQL: migração, isolamento, concorrência e quotas | 588 | [Job 127386625672](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635/job/127386625672) |
| Formulários, estados, acessibilidade e navegação | 574 | [Job 127386922218](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635/job/127386922218) |
| Ponte: ciclo de vida, efeitos e isolamento | 488 | [Job 127386625664](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635/job/127386625664) |
| Chromium | 130 | [Job 127386628352](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635/job/127386628352) |
| WebKit | 130 | [Job 127386625690](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067771635/job/127386625690) |
| **Total** | **4.678** | **Seis grupos aprovados** |

Não houve falhas ou testes pulados nos grupos selecionados. Cada job de
navegador desmarcou os 130 casos do outro motor; esses casos não entram
na contagem daquele job. O grupo de API registrou seis warnings.
O total representa execuções dos seis grupos, incluindo a execução dos
mesmos cenários nos dois navegadores; não é uma contagem de testes únicos
nem uma soma com verificações locais ou workflows gerais.

| Identidade | Revisão |
|---|---|
| Base publicada antes desta etapa | `32a90c0fd003c5fbac70c1bd541bf9ce20171040` |
| Candidata da PR 264 | `4bb3f980ae967bc57691bb4990dd80af2f9289e9` |
| Merge de teste efetivamente executado pelo CI | `afa2c747db9ee5512f37b130719f17a177b39acd` |
| Merge integrado na branch de produção `security/prelaunch-hardening-20261030` | `976698882c666250c54a54675a91cb13f2d7f937` |
| Árvore comum à candidata, ao merge de teste e ao merge de produção | `d30043c86ade07de6841db7eb451302815940bec` |

O merge da [PR 264](https://github.com/QGBALTIGO/orion-assistente/pull/264)
foi concluído. A igualdade da árvore foi verificada após o merge; não houve
alteração funcional entre o conteúdo qualificado e o conteúdo integrado.
O merge, isoladamente, não confirma que os serviços publicados já executam
essa revisão.

## CI geral — corte de 2026-11-05 às 16:57:55 UTC

A consulta completa retornou 19 workflows disparados pela PR: um dedicado,
registrado acima, e 18 gerais. Os 50 jobs gerais estavam assim no corte:

| Situação | Jobs |
|---|---:|
| Concluídos com sucesso | 37 |
| Falhas concluídas e comparadas com a base | 11 |
| Ainda em execução | 2 |

As 11 falhas concluídas foram comparadas com os jobs correspondentes da
candidata 2 da PR 263, `3c4014556aa82ba322ebd94debc6e56a6ee300e7`.
A comparação preservou identificadores de testes, multiplicidade e causas
das falhas; nos diagnósticos TAP da ponte, apenas UUIDs sintéticos foram
normalizados. Todas essas falhas já estavam presentes na base, sem falha
nova identificada nesse conjunto concluído. Isso não atribui resultado aos
dois jobs ainda ativos, cujos resultados finais não estavam disponíveis:

| Job pendente no corte | Evidência |
|---|---|
| Campos e layout — WebKit | [Job 127386635921](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067775299/job/127386635921) |
| FATIA 0 e 1 — pytest com PostgreSQL e Docker | [Job 127386620212](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42067770982/job/127386620212) |

Sete workflows existentes na base de comparação não foram disparados por
este delta; não são contabilizados como aprovados. A suíte geral da ponte
continua com 19 falhas herdadas em 840 testes, com 821 aprovados, embora o
grupo dedicado de transporte tenha aprovado seus 488 casos selecionados.
Não se declara a suíte geral nem o repositório inteiro aprovado, e não se
somam contagens de workflows sobrepostos.

Os registros consolidados são `stage13-ci.json` e
`stage13-general-ci.json`, preservados na evidência desta sessão. O corte
geral permanece explícito, sem substituir os resultados pendentes por
resultados anteriores.

As correções documentais que acompanham este módulo registram a conclusão
já observada da etapa 11 e a pendência da carteira e recargas da etapa 12.
Não alteram o código funcional dessas etapas.

## Publicação inicial e problema encontrado ao vivo

A API (`58faae6d-7729-4d07-b4ef-09a003a1e00f`) e o worker
(`6247b8dc-864c-406b-8da6-f632e1f58292`) publicaram o merge
`976698882c666250c54a54675a91cb13f2d7f937`, em SUCCESS e online 1/1.
Na consulta de 17:01:29 UTC, os cinco serviços estavam online, sem avisos,
crashes, operações pendentes ou mudanças preparadas. A ponte continuou no
deployment `91a9b644-b9f3-4d97-80f6-5db624275c47`, com árvore idêntica;
o deployment novo foi ignorado pelos watchpaths. As barreiras confirmaram
o schema 0071 já vigente. Não houve nova migração nesta etapa.

A conferência no Chrome conectado, após recarregar o painel, encontrou o
menu Aquecimento ausente. O console não apresentou erros. A condição da
rota usava `effective_plan.active`, que descreve a atividade do plano
comercial. Uma empresa ativa de assinaturas sem concessão comercial tem
esse campo falso, embora o backend conceda corretamente
`whatsapp_guidance.view`. Os cenários de browser iniciais usavam plano
Elite e, por isso, não detectaram o caso real sem plano.

A correção preserva a distinção entre atividade da empresa e situação
comercial: a rota verifica `tenant.status === 'active'`, retornado pelo
contrato real de `/api/v1/account/businesses`, além de proprietário,
workspace e permissão. A semântica de `effective_plan.active` permanece
intacta. As fixtures passam a representar empresa ativa sem plano, com
`effective_plan.active === false`; os casos de empresa inativa continuam
exercitando uma permissão antiga verdadeira para verificar o bloqueio.

A etapa 13 permanece aberta até publicar essa correção e conferir o guia
e o atalho no Chrome real. Nenhuma etapa posterior foi iniciada e nenhuma
mensagem, compra ou configuração de cliente foi efetuada na conferência.

### Regressão local da correção

O caso sem plano falhou antes da correção tanto no DOM (artigo ausente)
quanto no Chromium (rota `novidades` em vez de `aquecimento`). Depois da
troca da guarda, 15 casos DOM e nove Chromium passaram. Os seis layouts
registram empresa ativa, `effective_plan.active` falso e as permissões de
guia e Dispositivos verdadeiras. Os três bloqueios de acesso permanecem,
sem mutações, pedidos externos ou erros de página. Os nove casos unitários
de política também passaram, agora explicitando a diferença entre empresa
ativa sem plano e empresa ativa com concessão Elite.
