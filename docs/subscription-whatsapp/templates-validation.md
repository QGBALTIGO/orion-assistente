# Etapa 04 — Qualificação de Templates

Base anterior à entrega: `8146d5f7f50bb778d188f74b7e52452394870c50` (PR 250).
Observação e decisões: [templates-observation.md](templates-observation.md).

## Estado da etapa e revisão incorporada

A [PR 251](https://github.com/QGBALTIGO/orion-assistente/pull/251) foi mesclada
em `7164ce5ca9e4c795910ca5255f510f625dad0a11`. Sua árvore,
`ef30f3c3a5d0507c2f7dc25619bcb7f2b66d91ec`, é a mesma validada no candidato
`a0f87f4068b20ba14b83c5b6fbc9d2fb9c4d18fb`.

**Templates foi publicado e conferido ao vivo em 2026-11-04.** Os seis grupos
específicos de CI passaram; API, worker e ponte concluíram a implantação na
mesma revisão. O backup privado, a migração 0063 → 0064 e a inspeção no Chrome
conectado estão registrados abaixo. A etapa 05, Mensagens Enviadas, entrou em
observação da referência após o encerramento desta etapa.

## CI final da etapa 04

Fonte: [execução 41861913658](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41861913658),
com a mesma árvore incorporada pelo merge. Os resumos finais dos logs foram
conferidos; o job PostgreSQL da tentativa 2 também confirmou conclusão com
sucesso.

| Grupo | Resultado final | Evidência e escopo |
|---|---|---|
| Python | 1.198 aprovados | Contratos, API, regras, arquivos, permissões e verificações de release. |
| PostgreSQL | 147 aprovados em 136,15 s | PostgreSQL real com papéis separados; [tentativa 2, job 126704684853](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41861913658/job/126704684853). |
| Ponte WhatsApp | 309 aprovados | Protocolo real com sockets, provedores e destinatários sintéticos. |
| Interface DOM | 226 aprovados | Biblioteca, editor, simulador, conflitos e permissões. |
| Chrome oficial | 33 aprovados | Codecs reais, biblioteca → Chatbot → simulador e regressões das etapas anteriores. |
| WebKit | 33 aprovados | Os mesmos contratos de navegação e mídia, com dimensões e temas emulados. |

A primeira tentativa do job PostgreSQL foi interrompida pelo limite de tempo
durante a instalação de FFmpeg via `apt`, antes de iniciar o pytest. A segunda
tentativa utilizou a mesma revisão e concluiu os 147 testes; não houve mudança
de código, exclusão de cenário ou flexibilização de asserção para repeti-la.

## Comparação com as suítes gerais

A base de comparação tem árvore `f911128ea29637f7a1f3af326e01f764f597158e`,
igual à produção anterior da PR 250. A comparação foi feita pelos IDs dos casos
falhos, além dos totais.

| Suíte | Resultado final | Comparação com a base |
|---|---|---|
| [Python geral — job 126685693099](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41861913761/job/126685693099) | 7.783 aprovados, 170 falhas, 43 erros e 9 pulados | Zero novos IDs de falha/erro; seis falhas históricas de mídia/decoder deixaram de ocorrer. |
| [Campos e layout — WebKit](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41861913641/job/126685691971) | 141 aprovados e 52 falhas | Os 52 IDs falhos são idênticos aos da base. |

As 19 divergências novas encontradas na primeira rodada foram resolvidas: 14
ocorrências causadas pela ausência de FFmpeg/FFprobe no ambiente geral, uma
expectativa incorreta de status de upload e quatro unidades afetadas pela
ordem de importação das dependências de plano. Nenhuma delas reapareceu no
resultado final. Os detalhes das correções estão no histórico abaixo.

As falhas e erros restantes das suítes gerais continuam registrados como
problemas anteriores. A aprovação desta etapa combina os seis grupos
específicos aprovados com a ausência de novas identidades de falha ou erro na
comparação da mesma árvore.

## Escopo

A aba `templates` e a API `/subscription-whatsapp/templates` exigem
`templates.manage`, proprietário e empresa ativa de Serviço de assinaturas.
Biblioteca, Chatbot e simulador usam as mesmas identidades e revisões. A migração
aditiva `0064_subscription_templates` acrescenta os campos da biblioteca e os
tipos áudio/localização, sem criar conteúdo, regras, sessões ou mensagens.

O predeploy exige a revisão anterior, contrato de permissões e backup privado
verificado antes do DDL. O worker confere a estrutura e os papéis do banco antes
de iniciar. As allowlists anteriores reconhecem explicitamente 0064; não foi
adotado um teste amplo que aceite revisões desconhecidas.

## Evidência local anterior ao CI final

- O gate Python completo passou com **1.085 testes em 57,74 segundos**, incluindo
  API, regras, mídia, arquivos, permissões e gates de publicação. Uma asserção
  adicional do broker privado de áudio foi acrescentada depois dessa rodada e
  incluída no CI final registrado acima.
- O gate da ponte passou com **309 testes**, usando a biblioteca de protocolo
  publicada e fornecedores/sockets sintéticos. Inclui bytes criptografados dos
  novos formatos e a sequência áudio → legenda → mensagem posterior → localização.
- O gate DOM passou com **225 testes**, incluindo 22 da biblioteca e 30 do
  simulador. Os cenários incluem conflitos, repetição idempotente, acesso revogado,
  troca de empresa, respostas atrasadas, arquivos privados e isolamento do nicho.
- **38 casos PostgreSQL coletados**: CRUD, quota, idempotência, concorrência,
  autorização real, formatos preparados por codecs, leitura privada após grant,
  sucessores de efeitos, exportação/importação e barreira de catálogo 0064.
  Coleta local não equivale a execução no banco; a execução real posterior está
  registrada no quadro de CI final.
- **8 cenários Chromium passaram em 46,78 segundos**, com os oito cenários
  WebKit encaminhados ao CI posteriormente concluído. Incluem larguras de 390, 768 e 1440 px nos dois temas,
  biblioteca → Chatbot → simulador, decodificação de imagens/áudio/vídeo e
  download explícito de documento. As capturas foram inspecionadas sem corte
  horizontal. São navegadores reais com dimensões emuladas, não aparelhos físicos.
- Revisão independente da integração visual e do simulador sem bloqueador.
  As prévias usam somente Blob local. Documentos não são embutidos; localização
  não busca mapas externos. Uma sondagem real no Chromium confirmou que retirar
  um player da página e revogar seu Blob interrompe sua reprodução.
- A revisão de arquivos identificou e fechou a conversão silenciosa de OGG
  com vídeo em áudio. O teste usa um arquivo real com Theora/Vorbis; capas de
  álbum MP3 identificadas como `attached_pic` continuam aceitas. O conjunto de
  transporte passou com **30 testes em 2,89 segundos** após essa correção.

### Retomada dos validadores PDF, APK e áudio

A retomada encontrou a importação de `subscription_template_pdf` sem o módulo
correspondente no checkpoint. O módulo foi concluído com `pypdf==6.21.4` em modo
estrito, verificando cabeçalho/final, catálogo, páginas e referências dos objetos.
O processo de inspeção aceita até **10.000.000 bytes**, **20.000 objetos**,
**2.000 páginas**, profundidade de **64** e **100.000 entradas percorridas**;
streams decodificados pelo parser têm limite de **20.000.000 bytes**. No Linux,
o processo tem limites de **384 MiB de espaço de endereçamento**, **5 segundos
de CPU** e **10 segundos de tempo total**. Os bytes do documento não são
reescritos. Este fluxo aceita PDFs sem criptografia; PDFs criptografados,
inclusive os protegidos por senha, são recusados.

As regressões usam PDFs completos, com conteúdo, metadados, objetos nulos e
atualização incremental, além de casos truncados, referências inválidas e
stream incompleto. O APK valida o manifesto XML textual ou binário inteiro
com até **2.000.000 bytes** e profundidade **64**, sem instalar nem executar o
pacote. Foi reproduzido e corrigido também um ZIP/APK com DEFLATE corrompido:
o erro de descompressão agora retorna a resposta de validação **415**, em vez
de escapar como falha interna.

A rodada final focada passou com **175 testes em 9,38 segundos**:
`test_subscription_template_pdf.py`, `test_subscription_template_apk.py`,
`test_subscription_template_transport.py` e `test_subscription_chatbot_media.py`.
Inclui o OGG real com trilha de vídeo recusado e o MP3 com capa aceito. Essa
rodada complementa os gates do módulo; não representa envio a destinatários
reais, análise de malware ou validação de assinaturas PDF/APK.

## Histórico da preparação e dos ajustes

### Conferência da retomada

A árvore da base local foi comparada com a produção da PR 250 e coincide com
`f911128ea29637f7a1f3af326e01f764f597158e`. A branch efetivamente publicada é
`security/prelaunch-hardening-20261030`; `main` não é a origem desses serviços.

Na retomada, o gate completo de contratos passou com **1.176 testes em 61,75
segundos**. O ajuste posterior de ZIP/PDF foi coberto pela rodada focada de
175 testes acima e novamente pelo CI final. Ponte e DOM passaram com
**309** e **225** testes. A revisão da interface corrigiu o bloqueio visual
após conflito de revisão e a atualização da URL privada de um arquivo
preservado depois da reconciliação explícita; os **23 testes da biblioteca**
passaram após esses ajustes. Nenhuma dessas operações enviou mensagens reais.

Nesse checkpoint, PostgreSQL e WebKit ainda aguardavam o CI. Seus resultados
finais estão registrados acima; os números locais preservam a evidência da
preparação e não são somados aos totais do CI.

A primeira rodada da [PR 251](https://github.com/QGBALTIGO/orion-assistente/pull/251),
[run 41858318390](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41858318390),
passou com 1.198 casos Python, 309 da ponte, 226 DOM e 33 WebKit. O PostgreSQL
teve 145 aprovações e duas divergências nas expectativas: arquivo com conteúdo
inválido retorna 415, e o ACK terminal não inclui uma lista de efeitos. As
asserções foram alinhadas ao contrato, mantendo a verificação independente da
fila vazia e todos os controles de ordem, isolamento e mídia privada.

O Chromium distribuído pelo Playwright não concluiu a decodificação de uma
mídia; a saída original não identificava o tipo. O cenário agora conserva as
mesmas exigências e registra o estado/erro nativo do player em caso de falha.
O job passa a verificar os codecs e executar com o Chrome oficial, de acordo
com a [orientação do Playwright para codecs](https://playwright.dev/python/docs/browsers#media-codecs).
Essa preparação não alterou arquivos de mídia nem o código do produto.
A rodada final confirmou os 33 cenários no Chrome oficial e os 33 no WebKit.

A comparação da suíte geral no mesmo código identificou 19 diferenças novas:
14 ocorrências por ausência de FFmpeg/FFprobe no ambiente geral, a expectativa
415/422 já corrigida e quatro testes unitários afetados pela ordem de importação.
O workflow geral agora instala o mesmo decoder exigido pelo runtime. A unidade
de autorização monta as rotas a partir dos endpoints declarados, preservando
`Depends(authenticate)`, sem herdar dependências de plano adicionadas globalmente
por outros testes. A interferência foi reproduzida antes da correção; depois,
os 99 casos da unidade e as quatro variantes executadas após a inclusão prévia
dos guards passaram. A integração PostgreSQL mantém o router e os guards reais.
Não foram removidas asserções nem acrescentadas exclusões de testes.

## Implantação e conferência em produção — concluídas

O workflow `subscription-chatbot.yml` inclui a nova biblioteca e mantém os
contratos das etapas 01–03: Python, ponte Node, DOM, PostgreSQL 16/Redis 7 e
Chrome oficial/WebKit em celular, tablet e desktop, nos temas claro e escuro.
Fixtures de mídia são sintéticas e decodificáveis. A rede dos cenários é
limitada ao servidor de teste; não há envio para clientes ou consumo de provedor.

Os três deployments concluíram com `SUCCESS` no commit
`7164ce5ca9e4c795910ca5255f510f625dad0a11`, cuja árvore corresponde à validada
no CI. Horários de 2026-11-04 em `America/Cuiaba` (UTC−04:00):

| Serviço | Deployment | Resultado | Hora local |
|---|---|---|---|
| API | `317a2deb-4924-459a-8baa-d3c3e09e71ab` | `SUCCESS` | 12:33:43 |
| Worker | `e724cf94-5715-4de2-80ab-7c5e10a48f6d` | `SUCCESS` | 12:33:50 |
| Ponte WhatsApp | `830b966d-9c15-48a1-bbe4-db720b1cdda1` | `SUCCESS` | 12:32:30 |

Na conferência, a ponte apresentava **1/1 online e zero alertas**. Os horários
originais em UTC são, respectivamente, 16:33:43Z, 16:33:50Z e 16:32:30Z.

### Backup, banco e inicialização

Os logs da API confirmaram `private_backup_archive_verified` às **12:33:27**
(16:33:27Z), a aplicação Alembic **0063 → 0064** às **12:33:28** (16:33:28Z),
o contrato `schema_ready` e a inicialização da API às **12:33:40** (16:33:40Z).

A conferência de código confirmou que o predeploy da API chama
`backup_and_migrate_subscription_templates.main()`, que exige backup privado
verificado antes de `alembic upgrade 0064_subscription_templates`. O predeploy
do worker, por meio de `wait_for_single_whatsapp_schema.main()`, e seu
`WorkerSettings.on_startup` aguardam o contrato estrutural 0064 antes de consumir
tarefas. Os deployments e os logs acima complementam a conferência desse
encadeamento no código.

### Conferência no Chrome conectado

Na conta da empresa **BaltigoFlix**, foram confirmados:

- O item **Templates** no menu, a biblioteca vazia **0/100** e a pesquisa.
- Os cinco formulários: **Texto**, **Mídia**, **Figurinha**, **Recado de vídeo
  (PTV)** e **Localização**, com os campos e limites previstos.
- Legenda e mensagem posterior como campos separados.
- Ausência de erros no console durante a conferência.

O editor vazio foi fechado e a aba Templates foi deixada aberta. Não foram
criados templates, alterados dados nem enviadas mensagens reais nessa inspeção.
O CRUD, a integração biblioteca → Chatbot → simulador e o transporte com mídias
foram exercitados nos ambientes de teste sintéticos já discriminados acima.

A captura visual da tela publicada foi apresentada na conversa de trabalho;
ela complementa a conferência funcional e os registros desta entrega.

A entrega não afirma recebimento testado em um aparelho físico nem observação
dos comportamentos da referência que ficaram indisponíveis. Com os gates,
deployments e a inspeção ao vivo registrados, a etapa 04 está encerrada e a
observação da etapa 05 pode prosseguir.
