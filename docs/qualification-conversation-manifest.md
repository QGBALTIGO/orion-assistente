# Qualificação conversacional composta — fronteira e manifesto

## O que esta bateria comprova

`tests/unit/test_qr_generated_qualification.py` executa o `qr_ai.turn` real, em
`preview=False`, incluindo planejamento determinístico, composição, estado,
checagem de capacidades, revisão e consentimento. Não altera o aplicativo.
São **20 roteiros escritos, 500 composições parametrizadas e 10.000 caminhos de
quatro eventos**. Essas três contagens não são somadas nem chamadas de usuários,
requisições por segundo ou conversas humanas independentes.

O arquivo reutiliza a fixture factual de `test_qr_multi_flow.py`: catálogo,
agenda, reservas e Redis ficam em memória. O provedor é substituído pelo
`build_plan` real recebendo propostas sintéticas; a consulta de disponibilidade
recebe uma proposta explícita de intenção, serviço, profissional e dia.
Isso testa a validação e a decisão após a interpretação, **não a precisão do
modelo em interpretar aquela frase**. A chave Groq é removida do ambiente do
teste e chamadas HTTP síncronas/assíncronas falham imediatamente. Não há ASR,
WhatsApp, pagamentos, PostgreSQL, concorrência distribuída ou envio externo.

## 500 composições: 20 × 5 × 5

Cada caso tem um identificador estável `roteiro-contexto-catálogo` no pytest.
Não há variantes de maiúsculas, acentos ou pontuação para multiplicar números.
Os eixos são combinatórios: casos que percorrem a mesma decisão são equivalentes
naquele aspecto; não se afirma haver 500 algoritmos ou 500 frases diferentes.

| Eixo | Valores e diferença observável |
|---|---|
| Contexto inicial | Conversa nova; rascunho de Corte; rascunho de Barba; rascunho de ambos; resumo anterior encerrado por um turno real de recusa |
| Catálogo aberto | Corte e Barba atendidos pelos dois profissionais, horários factuais disponíveis |
| Só informações | Perfil permite consultar catálogo, mas não agendar/cancelar; não pode ler agenda nem criar hold |
| Sem horários | Serviços e profissionais válidos, mas a grade factual está vazia; não pode apresentar confirmação ou criar hold |
| Vínculos separados | Bruno atende só Corte e Breno só Barba; nenhum profissional atende o combo; não pode criar combo com vínculo inválido |
| Serviço literal adicional | Existe também **Corte de Barba**, com identidade/preço/duração próprios; a pergunta literal consulta esse item, sem confundi-lo com Corte + Barba ou Barba |

Os 20 roteiros e seus oráculos principais:

1. Preço de Corte, preservando a seleção anterior e sem escrita.
2. Preço literal de corte de barba, respeitando a presença/ausência do serviço específico.
3. Preço de Corte + Barba, sem transformar consulta em compra.
4. Negação explícita de agendamento seguida de preço de Barba.
5. Correção dentro da pergunta de preço: combo → somente Barba.
6. Pedido completo de Corte: apenas hold quando capacidade, vínculo e grade permitem.
7. Pedido completo de combo: composição correta ou bloqueio factual.
8. Correção de horário duas → três: apenas o horário final pode criar hold.
9. Correção de serviços combo → Corte: apenas a seleção final pode criar hold.
10. Adição de Barba: combo de fatos exatos ou bloqueio por vínculo/grade/capacidade.
11. Consulta de agenda: lê grade quando permitido, sem hold/confirmação.
12. Pedido humano sobrepõe agendamento; `Sm` posterior não executa nada.
13. Falha do provedor: preserva contexto, sem leitura de agenda ou escrita parcial.
14. Alternativa entre serviços: não escolhe arbitrariamente nem cria hold.
15. Alternativa entre profissionais: não escolhe arbitrariamente nem cria hold.
16. Recusa do resumo seguida de `Sm`: não reutiliza autorização encerrada.
17. `Sm` sem resumo: não inventa consentimento para uma operação.
18. Expiração explícita do resumo seguida de `Sm`: não confirma hold expirado.
19. Solicitação de cancelamento de reserva própria, recusa e `Sm`: não cancela.
20. Resumo novo e `Sm` posterior: confirma exatamente o hold factual permitido;
    sem capacidade, vínculo ou vaga, continua sem confirmação.

Em cada resposta também se verificam limite de 1.200 caracteres, histórico com
no máximo oito entradas, seleção pertencente ao catálogo e ausência de escrita
de nome de cliente não solicitado. A fixture de confirmação retorna os fatos
do hold realmente preparado no teste, não um preço/horário constante independente.

## 10.000 sequências de invariantes

O alfabeto contém dez eventos semanticamente diferentes: preço de Corte; preço
de Barba; escolher Corte; adicionar Barba; retirar Corte; consultar horários;
pedir humano; recusar; afirmar `Sm`; solicitar cancelamento. A exploração é
exaustiva para comprimento quatro: **10⁴ = 10.000 caminhos únicos**.

Nenhum evento fornece um horário operacional para reservar; a consulta de
agenda fornece dia/profissional, mas permanece informativa. O contato sintético
não possui reservas canceláveis. Portanto, o oráculo permite informações,
rascunhos, encerramento e atendimento humano, mas proíbe qualquer escrita de
reserva, confirmação ou cancelamento, hold e marca de operação verificada.
Uma sentinela de outra chave de empresa/contato não pode ser alterada. Essa
sentinela prova o escopo do estado em memória, **não substitui RLS real**.

Prefixos idênticos compartilham a execução: cada aresta distinta chama o `turn`
real e seu estado Redis produzido é restaurado para cada próximo evento.
São **10 + 100 + 1.000 + 10.000 = 11.110 chamadas reais de `turn`**, em vez de
repetir 40.000 chamadas com prefixos iguais. O teste verifica a cardinalidade
do conjunto de caminhos, a contagem de chamadas, que houve consultas de grade
e decisões de rascunho, humano, histórico vazio e pedido encerrado. As invariantes
são verificadas em **cada nó executado**, não apenas ao gerar a lista.

Essa exploração cobre recombinação de eventos sem autoridade. As transições
positivas com hold/consentimento estão na matriz de 500 e nas suítes existentes;
não se afirma que os 10.000 caminhos tenham todos criação e confirmação.

## Inventário existente, sem reclassificar helpers como conversas

Leitura AST + coleta pytest em 18/10/2026, base
`ced299e5bb26d145b78ce3173bebf0c8837e3d45`, antes deste arquivo novo:

| Arquivo em `tests/unit/` | Funções de teste distintas | Casos coletados |
|---|---:|---:|
| `test_qr_multi_flow.py` | 62 | 135 |
| `test_qr_conversation_matrix.py` | 29 | 29 |
| `test_qr_followup_context.py` | 28 | 70 |
| `test_qr_hold_recovery.py` | 18 | 37 |
| `test_qr_recovery_guards.py` | 6 | 17 |
| `test_qr_cancellation.py` | 50 | 107 |
| Subtotal dos seis arquivos de fluxo/guardas | 193 | 395 |
| `test_qr_understanding.py`, planejamento puro separado | 32 | 73 |

Os seis primeiros também incluem contratos de helpers/guardas: a contagem
395 é de **casos coletados**, não 395 conversas completas. A coleta não executou
novamente essa bateria histórica. Renderização pura, variantes de pontuação,
lookup e testes de frontend não foram usados para atingir o mínimo novo.

## Execução e limites de aceite

```sh
python -m pytest tests/unit/test_qr_generated_qualification.py -q -s
```

Resultado focal local: **501 passed** — 500 composições e um teste exaustivo
com 10.000 caminhos/11.110 chamadas. O teste exaustivo imprime as contagens
executadas e os estados observados para o log de CI. É preciso executar o mesmo
arquivo no CI da revisão final; o sucesso local não declara publicação.

A bateria HTTP + PostgreSQL + Redis de `qualification_load.py`, a migração
populada e as 20 jornadas reais Chromium têm evidências separadas. Qualidade
de áudio, compreensão pelo provedor ativo, entrega/legibilidade no aparelho,
pareamento de canais e janela de 12 horas continuam fora desta prova.
