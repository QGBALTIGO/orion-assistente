# Etapa 02 — qualificação do Chatbot

## Escopo

PR [249](https://github.com/QGBALTIGO/orion-assistente/pull/249), sobre a etapa
Dispositivos publicada na revisão `976fa7a095b053e5589bab52eb058187ebba858c`.
As observações da referência e os limites da comparação estão em
[chatbot-observation.md](chatbot-observation.md).

O módulo usa a permissão `chatbot.manage` do perfil `subscription_services`.
Empresas de outros nichos continuam com as permissões e a navegação anteriores.
Os dados de testes são sintéticos. A qualificação não envia mensagens a
contatos reais nem altera configurações da conta BotBot de referência.

## Contratos verificados

| Área | Verificação |
|---|---|
| Regras | Correspondência, precedência, coringas, janelas locais, cópia inativa, filtros, revisão e idempotência. |
| Execução | Ordem por contato, áudio/transcrição canônica, atraso, rechecagem de autorização e interrupção após revogação. |
| Transporte | Protocolo nativo construído pelo SDK publicado, mídia privada e cache cifrado antes do relay; sockets e uploads substituídos por provedores sintéticos. |
| Resultado incerto | Uma tentativa possivelmente executada não é repetida; recibos HTTP são reconciliados sem nova chamada; caudas impossíveis são encerradas sem reenviar a origem. |
| Menus | Seleção vinculada à mensagem, contato, dispositivo e revisão; texto recebido não se torna uma ação arbitrária. |
| Arquivos | JSON/ZIP versionado, grafo fechado, referências remapeadas, hashes, limites, ZIP malformado, expansão excessiva e metadados de mídia. |
| Importação | Prévia sem escrever regras ou mídia; aplicação revalida permissões, revisão, quotas, dispositivos e conteúdo. |
| Restauração | Adicionar ausentes, mesclar e snapshot exato; backup de segurança e publicação do grafo na mesma transação; nonce e heartbeat para recuperação. |
| Downloads | Nova autorização antes de liberar bytes; erro HTTP não pode ser salvo como arquivo válido. |
| Banco | PostgreSQL 16, papéis separados, FORCE RLS, permissões por coluna, chaves compostas por empresa e concorrência real. |
| Publicação | Migração aditiva 0063 somente após backup privado verificado; API e worker aguardam o contrato completo. |
| Interface | Componentes reais Orion, três larguras, dois temas, Chromium/WebKit, foco, rascunhos, erros e troca de contexto. |

## Evidência de execução

O primeiro checkpoint integrado `acb7e4fe75eef44d32788a1bbc9d35ca7449cc6c`
passou em 851 testes Python, 300 Node e 166 DOM. O PostgreSQL apontou uma
fixture que regenerava o UUID da ação ao simular um replay; ela foi corrigida
para repetir o payload exato e também provar a recusa de um payload diferente.
O ambiente dos testes de navegador recebeu o Pydantic usado pelos DTOs reais.

O checkpoint completo `45914aa2583c548acaeee6ff38127178fc75485a` acrescenta a
orquestração de arquivos, 19 cenários de archive no PostgreSQL e as correções
de recuperação. Os seis grupos passaram na execução de qualificação:
[run 41827594551](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41827594551).

| Grupo | Resultado |
|---|---:|
| Python/API/motor/arquivos/publicação | 874 aprovados |
| Node/ponte | 300 aprovados |
| DOM/interface | 166 aprovados |
| PostgreSQL real | 92 aprovados |
| Chromium | 18 aprovados |
| WebKit | 18 aprovados |

A revisão seguinte corrige o tratamento dos downloads no navegador e a
sincronização do teste de carrossel descrita abaixo. Na validação local desse
ajuste passaram 171 testes DOM, os 13 de runtime e o cenário Chromium
390 px/tema escuro, que abre o JSON, o ZIP exportado e o backup ZIP recebidos.
Os checks da PR registram a qualificação completa da revisão final antes do
merge. O deployment deve ser conferido pela revisão exata nos três serviços.

## Comparação dos gates compartilhados

Falhas antigas foram comparadas com o head da PR 248,
`5de40209cb90e1b4f3af6e01c66e38304e13eed7`; não foram removidas nem ocultadas.

- O reparo histórico de identidade permanece limitado ao schema 0052. Os
  três cenários de CLI já falhavam no baseline, com 313 testes aprovados.
  Essa autorização histórica não foi ampliada para liberar o novo módulo.
- O smoke legado de Planos espera `.op-payment-body` do checkout anterior.
  A implementação Pix publicada usa outro fluxo. Teste, JavaScript, CSS e
  fixture têm os mesmos blobs no baseline e nesta etapa. Os 72 cenários
  Chromium de permissões passaram; a falha do smoke foi reproduzida em
  320 px, tema claro, sem erro de JavaScript.
- Os testes VM antigos de áudio e do painel QR apresentam falhas anteriores
  à etapa 02. O workflow completo continua executando esses testes. Os gates
  que descobrem todos os testes Node agora instalam as dependências do
  transporte, necessárias para os novos testes de protocolo real.
- No checkpoint completo, o gate FATIA/QR repetiu exatamente as 49 falhas do
  baseline, com 582 aprovações. Appointment communications repetiu as 30
  falhas antigas e apontou uma espera insuficiente no novo teste de carrossel:
  400 turnos de `setImmediate` terminaram em 64 ms. O teste passou a aguardar
  a Promise do recibo explícito do efeito, com timeout de dez segundos,
  preservando as verificações de protocolo, escopo, mídia e aceitação.

## Limites da evidência

O formato portátil Orion tem versão própria. Não há alegação de importar
arquivos BotBot cujo formato interno não foi obtido. O limite portátil é
10 MiB; snapshots internos maiores continuam restauráveis, mas um download
que exceda esse teto deve apresentar erro explícito.

Os testes de protocolo e do SDK não comprovam entrega nem renderização em
todos os clientes WhatsApp, especialmente carrosséis. As simulações de
largura dos navegadores não substituem um teste em aparelho físico. Nenhum
resultado sintético é apresentado como mensagem entregue a uma pessoa.

## Publicação verificada

PR 249 mesclada em `112e92ea79240a1980f484bf2787c4400bd5418d`, com árvore
`46e94a41fa12f7b01e05fc3d002ff37babffc0ba`, idêntica à revisão qualificada.
O [run final 41828950795](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41828950795)
passou em 874 testes Python, 300 Node, 171 DOM, 92 PostgreSQL, 18 Chromium
e 18 WebKit: 1.473 execuções aprovadas. Os dois gates Node compartilhados
repetiram somente as 30/49 falhas legadas, com o novo teste de carrossel aprovado.

Em 2026-11-04, os três serviços Railway estavam em `SUCCESS`, na revisão
acima, cada um com uma réplica em execução e nenhuma réplica caída. Não havia
operações de deploy pendentes. A API registrou
`orion_subscription_chatbot_schema_ready`.

| Serviço | Deployment |
|---|---|
| API | `c72c2058-e6b6-4016-9772-0cd6f6b9b29b` |
| Worker | `1a24952a-7a12-4628-bd3b-f1b7592ce781` |
| Ponte | `ec2ec483-c877-4437-9214-50afab4f2986` |

No Chrome autorizado, a empresa do nicho de assinaturas carregou a navegação
com Dispositivos e Chatbot, métricas reais, catálogo de dispositivos e o editor
completo. Nenhuma regra de teste foi salva nessa conta para essa conferência.
