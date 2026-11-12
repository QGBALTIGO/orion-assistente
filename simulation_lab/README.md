# Orion Simulation & Reliability Lab

Laboratorio original, gratuito e isolado para o nicho de barbearias e saloes.
Este diretorio NAO roda tarefas de producao, nao acessa banco real e nunca envia
WhatsApp. Nao substituir as suites PostgreSQL e de interface ja presentes no repo.

## Como executar no PC

Repositorio a partir da branch real de Railway:
security/prelaunch-hardening-20261030

1. Usar Python 3.12.
2. Gerar os 100.000 cenarios, 10.000 jornadas, 2.048 estabelecimentos e 32.768 clientes:

    py -3.12 -m simulation_lab.orion_lab generate --out E:\OrionSimulationWork\lab-data

3. Executar o verificador deterministico de referencia:

    py -3.12 -m simulation_lab.orion_lab reference --out E:\OrionSimulationWork\lab-data

4. Reproduzir 38.000 casos nas funcoes PURAS reais do motor de reservas da Orion (sem banco ou mensagens):

    E:\OrionSimulationWork\.venv\Scripts\python.exe -m simulation_lab.real_engine_replay --out E:\OrionSimulationWork\lab-data

5. Conferir quotas, hashes SHA-256 e diversidade:

    py -3.12 -m simulation_lab.orion_lab verify --out E:\OrionSimulationWork\lab-data

6. Testar o codigo do laboratorio, sem banco nem internet:

    py -3.12 -m pytest -q tests/unit/test_simulation_lab.py

7. Testar invariantes geradas por fast-check:

    npm ci --prefix simulation_lab/property
    npm test --prefix simulation_lab/property

8. Abrir o arquivo lab-data/report.html no navegador para visualizar a cobertura.

Dados gerados sao gravados FORA da arvore Git por padrao. A pasta
simulation_lab/artifacts esta ignorada pelo Git. Nunca adicionar arquivos de
segredo, dump de cliente real, tokens ou logs de producao ao corpus.

## Como interpretar contagens

- generated_scenarios: casos sinteticos construidos, nao chamadas de API.
- unique_semantic_signatures: assinaturas da matriz de comportamento e regras,
  sem depender do nome ou ID de cliente ou empresa.
- generated_journeys: roteiros multietapas, nao trajetorias executadas.
- executed_deterministic_reference_cases: verificacoes internas do simulador.
  Sao oraculos de referencia e NAO testam Orion.
- orion_app_tests_executed: zero ate haver recibo independente dos testes do app.
- postgres_integration_tests_executed: zero ate rodar PostgreSQL 16 isolado.
- ai_conversations_executed: zero ate rodar modelo real com custo consentido.
- end_to_end_tests_executed: zero ate rodar Playwright contra ambiente de teste.

O gerador produz canais, horarios, duracoes, combos, regras, profissionais,
recorrencias, falhas, permissoes, linguagem informal e texto de cada solicitacao.
As regras esperadas sao referenciais e precisam de validacao contra o backend
real. A grade Peixoto 09:00, 09:40 e 10:20 tem caso fixo separado.

## Testes reais adicionais, com suas restricoes

- Os testes atuais de testes/unit, tests/integration, tests/load e ui-tests
  permanecem fonte principal de evidencia funcional. O CI existente do repo
  tem PostgreSQL 16 e Redis descartaveis para integracao; este laboratorio
  nao deve usar o banco da Railway em producao.
- A bateria Playwright de leitura publica nao pode criar reservas e deve gerar
  evidencias apenas para renderizacao, scroll e navegacao sem efeitos.
- O script k6/availability.js aceita EXCLUSIVAMENTE localhost; seus numeros
  representam latencia de /health, NAO capacidade de agendamentos. Para carga
  autenticada e corridas de escrita, criar staging com dados sinteticos.
- O workflow de CI do laboratorio e independente dos deploys Railway/Vercel,
  permitindo registrar artefatos no pull request sem alterar os serviços.
- Regressao de P0 (vazamento, dupla reserva, corrupcao financeira, outro
  destinatario) bloqueia publicacao independentemente de media estatistica.

## Open source: decisao de integracao

Ferramentas selecionadas:

- fast-check, MIT: integrado como teste de propriedades com sementes fixas.
- Playwright, Apache-2.0: testador visual opcional e isolado do backend.
- Promptfoo, MIT: candidato a avaliar respostas de IA apos existir staging,
  provedor de teste e limite explicito de custo. Nao executar contra cliente.
- Tau2-Bench, MIT: referencia conceitual para jornadas ferramenta-usuario;
  nao trazer dependencias pesadas enquanto o harness existente cobre os fluxos.
- k6, AGPL-3.0: executar como ferramenta externa de carga local; nao incorporar
  seu codigo no produto.
- MiroFish e MiroFish-Offline, AGPL-3.0: nao embutidos no SaaS; alem de licenca
  copyleft a versao Offline requer Ollama e Neo4j e mais memoria/VRAM. Se
  usado futuramente, executar em ambiente independente e sem dados reais.
- Opik, Apache-2.0, e Langfuse, MIT exceto diretorios ee: nao implantar
  novos servicos em producao sem necessidade demonstrada; recibos JSON,
  traces ja existentes e correlacao bastam para primeira fase.

Verificar sempre as licencas atualizadas e dependencias transitivas antes de
redistribuir, integrar codigo ou hospedar um servico comercial derivado.

## Proximas portas de qualidade (NAO concluido pelo corpus sozinho)

1. Executar regressao integra com PostgreSQL real, migracoes e RLS.
2. Testar 2, 10, 50, 100 e 500 operacoes concorrentes em staging.
3. Executar journeys contra app real, via site, painel, worker e WhatsApp mock.
4. Avaliar respostas com modelo de IA real, informar N, custo e erro.
5. Corrigir P0/P1 reproduzidos, criar regressao e repetir a matriz.
6. Validar Playwright nas resolucoes representativas, inclusive screenshots.
7. Fazer smoke test do deploy isolado, revisar rollback, entao liberar producao.

Metas: zero P0 no escopo testado; taxa de intencao >= 98% em amostra identificada;
nota de respostas >= 4,5/5 em amostra identificada. Metas NAO sao resultados.

## Transparencia de execucao inicial (12/11/2026)

- Corpus local gerado e 100.000 oraculos do modelo de referencia executados.
- 44 testes unitarios novos do laboratorio, incluindo funcoes reais do motor, passaram no Windows.
- 38.000 casos foram executados contra as funcoes PURAS reais de reservas (22.000 agenda, 10.000 servicos e 6.000 recorrencias): 38.000 aprovados, zero operacoes PostgreSQL.
- Playwright Chrome: 13 testes de leitura aprovaram site publico e login em diferentes dispositivos/temas.
- A suite de UI preexistente apresentou 1.680 aprovados e 22 reprovados; entre eles testes de assinatura fora do escopo.
- A CI original do QR Bridge acusou 19 falhas do harness panel-ui, nao alterado por este laboratorio; trata-se de bloqueio de regressao ainda sem conclusao de causa raiz.
- fast-check: 7 testes aprovados, com 60.000 amostras sinteticas.
- A suite Python inteira do repo no Windows relatou 9.603 passes, 94 falhas,
  11 erros e 15 skips. NAO classificar tudo como defeito de producao: muitos
  testes precisam de Linux, servicos isolados ou binarios opcionais.
- Nenhuma chamada real de IA, nenhuma mensagem real enviada, nenhum banco de
  producao modificado por este laboratorio.
- Nao publicar em producao enquanto o baseline critico nao estiver validado.