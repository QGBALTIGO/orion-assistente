# Etapa 03 — Qualificação de Testar Chatbot

Base publicada: `112e92ea79240a1980f484bf2787c4400bd5418d` (PR #249).
Observação: [chatbot-test-observation.md](chatbot-test-observation.md).

## Implementação

A rota `testar-chatbot` pertence somente a `subscription_services` e exige
`chatbot.manage`. O atalho nos detalhes de uma resposta abre a conversa com
dispositivo e regra inicial. O botão anterior de envio real permanece separado.

O servidor usa o matcher e compilador publicados sobre cópias do grafo, sem
alterar o motor de eventos. A simulação inclui regras inativas e funciona com
dispositivo desconectado. Ela não consome janelas de atendimento ou frequências,
não executa URLs externas e não cria jobs ou efeitos de WhatsApp.

Cada pedido revalida proprietário, empresa, dispositivo e revisão. Listas usam
um contexto local e tickets autenticados de curta duração, com finalidade
separada dos menus reais. A leitura de mídia exige autorização antes e depois
de obter os bytes privados. Não há nova migração, dependência ou variável de
ambiente nesta etapa.

## Evidência local disponível

- Backend: o gate Python completo passou com **936 testes em 56,48 s**, incluindo
  **62 casos novos** do simulador, matcher/API, permissões, dispositivos e
  contratos dos nichos existentes. A primeira execução focada havia passado
  com 255 testes. Compilação Python sem erros.
- DOM: **196 testes passaram em 7,54 s**, sem falhas ou casos ignorados. Inclui
  23 casos novos da conversa e dois novos cenários do atalho da etapa 02.
- Chromium: **7 cenários passaram**, cobrindo 390/768/1440 pixels nos temas
  claro/escuro e um fluxo de corrida/revogação/isolamento. Capturas de celular,
  tablet e desktop foram inspecionadas. O JPEG privado foi decodificado pelo
  navegador; não houve erro JavaScript, origem externa ou envio real.
- WebKit: os sete cenários equivalentes serão executados no CI; esse runtime
  não está instalado localmente. Larguras simuladas não substituem aparelho
  físico nem teclado virtual real.
- PostgreSQL: **17 casos coletados**, preparados para o banco descartável do
  CI. Coleta não representa execução no banco.
- Revisão independente da integração do painel: rota, deep link, isolamento
  por empresa e desmontagem ao sair/trocar contexto, sem bloqueador encontrado.

## Validação que bloqueia a publicação

O workflow `subscription-chatbot.yml` executa os contratos Python, a ponte
existente, os testes DOM, PostgreSQL 16 e os fluxos Chromium/WebKit em celular,
tablet e desktop nos dois temas. O PostgreSQL compara linhas completas de 17
tabelas antes/depois da simulação; a saída de rede para provedores é bloqueada.

Os cenários cobrem texto, correspondência, inativas, referências, listas,
arquivos normalizados, URL/carrossel declarativos, 400 unidades UTF-16, revisão
obsoleta, perda de acesso e respostas tardias. A publicação requer resultados
do commit exato, deploy saudável e conferência posterior no painel publicado.

Os resultados finais do CI e da publicação serão registrados após a execução;
este documento não afirma que essas verificações já ocorreram.

## Ajustes encontrados pelo primeiro CI

O primeiro head da PR 250 passou em Python (936), Node (300), DOM (196) e
Chromium (25 cenários). A execução revelou três problemas nas fixtures e na
descoberta de testes, corrigidos antes da publicação:

- O nome do E2E terminava em `_test.py`, portanto a suíte geral também tentou
  coletá-lo sem o runner Playwright. O arquivo foi renomeado para
  `browser_subscription_chatbot_simulator.py`, mantendo execução explícita no
  gate de navegadores. Não foi alterada a descoberta global nem adicionada
  dependência de navegador aos testes gerais.
- O interceptor de rede do WebKit classificou um Blob criado pela própria
  página como origem externa. O artefato registrou somente esse Blob local e
  nenhum erro JavaScript. A fixture passa a reconhecer exclusivamente Blobs
  da origem local do servidor de teste, mantendo o bloqueio de origens remotas
  e a asserção de decodificação real do JPEG.
- O cenário PostgreSQL de substituição de dispositivo mantinha uma conexão
  no bridge falso depois de remover seu lease no banco descartável. A fixture
  foi alinhada ao estado desconectado; a autorização da API permaneceu intacta.

Esses ajustes não modificam o comportamento de produção. O novo commit deve
executar novamente os gates antes do merge.
