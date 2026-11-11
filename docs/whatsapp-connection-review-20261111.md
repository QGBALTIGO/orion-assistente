# Revisão da conexão WhatsApp em todas as empresas — 11/11/2026

## Evidência de produção

- A BaltigoFlix voltou a responder após a bridge assumir o deploy `b0b3709d4ba7c8a96bd47b1292b8b5849f23bd81`. O usuário confirmou a resposta no aparelho. Um efeito novo ficou `accepted` com ID do provedor; as tentativas anteriores às 17h01, 17h06 e 17h07 (MS) ficaram `not_sent`, sem ID do provedor e sem repetição.
- O socket da BaltigoFlix estava aberto, com zero entradas bloqueadas e pendentes na geração atual. O deploy de diagnóstico não alterou a regra nem o transporte. Portanto, a causa exata das falhas anteriores não foi provada; uma retomada saudável da sessão pode tê-las removido.
- A fila histórica do canal principal é independente. Esta revisão não libera registros retidos nem declara recuperação do canal principal.

## Caminhos revisados

| Camada compartilhada | Verificação e melhoria |
| --- | --- |
| Vínculo e sessão | A mesma bridge mantém identidade cifrada no volume, usa concessão SQL com responsável e geração e recusa nova identidade quando o estado preservado é incompleto. Reconexão distingue falha transitória, logout e substituição de sessão. Nenhuma credencial foi apagada ou desvinculada. |
| Entrada | Eventos recebem admissão durável por empresa, dispositivo, geração e revisão. Entrada bloqueada impede efeitos automáticos; registros com trabalho incerto não são reproduzidos. |
| Saída de todas as empresas e nichos | `channel-session` é o transporte comum. A consulta read-only ao WhatsApp agora tem limite de tempo e uma segunda tentativa apenas quando a consulta lança erro ou expira. Cada tentativa verifica a concessão vigente. Resultado negativo de identidade é definitivo para a operação; nenhum relay é repetido. |
| Estado visível | Duas falhas recentes de preparação antes do relay marcam a resposta como degradada, mesmo com socket aberto. O motivo não contém número, conteúdo ou erro privado do provedor. Uma resposta nova aceita limpa o sinal. O sinal acompanha a retomada da sessão até haver sucesso. |
| Diagnóstico | A etapa da falha do chatbot inclui somente tipo da ação e identificadores de empresa e canal. Não registra telefone, mensagem, mídia, chaves nem texto bruto de erro. |

## Qualificação local

- Testes específicos cobrem consulta transitória, expiração, revogação, resultado negativo sem repetição, degradação após duas falhas, recuperação após resposta aceita, sessão e legado.
- A matriz sem dois arquivos alheios a esta mudança passou: 1.305 testes, zero falhas e cinco ignorados. Na suíte total, 1.385 passaram, 20 falharam e cinco foram ignorados: 19 falhas em `panel-ui.test.mjs` (arquivos de interface não alterados; não comparadas nesta execução com a base) e um teste TLS impedido pela ausência do executável OpenSSL neste Windows.

## Limites da conclusão

Nenhum software garante ausência total de falhas no WhatsApp, na rede ou no provedor. O aceite do provedor não comprova entrega de todas as mensagens. A causa individual das três falhas anteriores à retomada não foi registrada pela versão antiga. A publicação desta melhoria precisa de deploy bem-sucedido e observação de efeitos novos sem reprocessar os anteriores.
