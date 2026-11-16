# 05 — Mensagens Enviadas: observação e contrato

## Fonte e limite da observação

Observado no Chrome conectado em 2026-11-04, após publicação e conferência
de Templates (PR 251): `https://botbot.chat/user/logs?channel=whatsapp`.
Foram usados somente navegação, filtros de leitura e paginação. O botão
Reenviar não foi acionado; seu resultado operacional e eventual confirmação
JavaScript não foram observados. Nenhum dado de contato, credencial, mensagem
privada ou identificador da conta de referência integra este registro.

| Evidência | Comportamento observado |
|---|---|
| sent-summary | Cartão Mensagens com total, Hoje e Últimos 30 dias. Cartão Transcrição de Áudio com total, Este mês e Restante. |
| sent-filters | Dispositivo, situação, número de contato e pesquisa de mensagens/contato/tipo. Aplicação pelo botão Filtro; URL GET conserva `channel=whatsapp`. |
| sent-status | Situações: todas, falhou, falhou — pode reenviar. Não foi observado filtro de entrega/leitura. |
| sent-clear | Filtros ativos mostram Limpar filtros, que retorna à listagem geral do canal. |
| sent-table | Colunas Mensagem de, Mensagem Para, Mensagem, Tipo de Solicitação, Enviado Em e Ação. Recebido e Enviado são identificados separadamente. |
| sent-device | Remetente apresenta telefone, nome atual do dispositivo e identificador. |
| sent-origin | Foram observados chatbot, API, transcrição de áudio e resposta de lista. Isso não implica que o Orion tenha todas essas origens implementadas nesta etapa. |
| sent-time | Data/hora absoluta em português e indicação relativa. |
| sent-pagination | 30 linhas por página; números, anterior/próxima e páginas finais. Página 2 preserva situação na URL. |
| sent-global | Cartões permanecem globais ao filtrar. O total de logs da tabela acompanha o filtro. |
| sent-empty | Pesquisa sem correspondências apresenta mensagem vazia específica dos filtros. |
| sent-actions | Sucesso não mostra ação. Falhas de número inválido observadas não oferecem reenvio; algumas falhas de conexão e de estabelecimento de confiança oferecem. |
| sent-retry-form | Reenviar é um submit POST para `/user/logs/{id}/resend`. Não foi acionado. Ausência de atributo HTML de confirmação não prova ausência de confirmação JavaScript. |
| sent-retention | Havia registros com mais de 90 dias. Isso não demonstra retenção ilimitada. |

Não foram observados exportação, exclusão, seleção em massa nem filtro por
intervalo de datas nesta tela. Não inferir que a referência implemente
idempotência, autorização transacional ou o tratamento de resultados incertos
definidos abaixo: essas são decisões independentes do Orion.

## Interface e acesso no Orion

- Exclusivo do nicho persistido `servico_assinaturas`, proprietário atual e
  empresa ativa; feature `sent_messages.manage`.
- Página `mensagens-enviadas`, controller independente, componentes e
  identidade visual do Orion.
- API `/api/v1/tenants/{tenant_id}/subscription-whatsapp/sent-messages`.
- Cartões agregados independentes da página; tabela de 30 tentativas, filtros
  de dispositivo/situação/contato/texto, paginação e limpeza.
- Fuso da empresa para datas absolutas, hoje e mês. Timestamps de transporte
  continuam UTC. O saldo técnico de áudio renova à meia-noite UTC e informa
  explicitamente seu período diário.
- Dados vazios, carregando, falha de consulta e acesso revogado são estados
  diferentes. Respostas antigas de outro filtro/empresa não podem reaparecer.
- A leitura não envia mensagens, executa ASR/HTTP nem consulta mídia externa.

## Unidade do histórico e métricas

Uma linha é uma tentativa persistida de mensagem: `send_text` ou
`chatbot_action` de tipo nativo. Webhook, rejeição de chamada, transcrição,
ação HTTP, configuração e simulação não são mensagens. Um áudio com legenda
separada e texto posterior pode gerar três linhas; um reenvio gera outra.
O teste real de resposta do Chatbot é identificado como teste, enquanto o
simulador sem efeitos continua fora deste histórico.

Os cartões de mensagens contam esses registros, incluindo falhas e reenvios.
O texto auxiliar esclarece "envios e tentativas registrados". `accepted`
significa aceitação pelo transporte; não prova recebimento, entrega ou leitura.
O resultado incerto é apresentado separadamente e nunca como não enviado.

A soma de transcrição usa somente duração decodificada de resultados completos
persistidos. Registros antigos sem medição continuam desconhecidos, sem
preenchimento a partir de reserva ou duração declarada pelo cliente. O saldo
diário é o limite individual menos as reservas do dia; consumo global de
outras empresas não é exposto nem apresentado como franquia individual.

## Contrato de leitura

`GET /sent-messages` aceita `page` (inteiro positivo), `device_id`, `status`
(`""`, `failed`, `failed_resendable`), `contact`, `q` e `snapshot`. A primeira
página captura um instante do banco; as seguintes preservam esse limite de
criação e a ordem `created_at DESC, id DESC`. Atualizar ou trocar filtros
inicia um novo snapshot. Isso evita deslocamento por novas inserções; o
resultado operacional de uma tentativa pode continuar evoluindo e alterar
sua correspondência ao filtro de situação.

Resposta: `items`, `page`, `per_page=30`, `filtered_total`, `total_pages`,
`snapshot`, `timezone`, `generated_at`, `devices`, `counts`, `transcription`
e `capabilities`. Os cartões não usam o total filtrado. `GET /{effect_id}`
retorna `item`, `timezone` e `generated_at` para leitura detalhada e
confirmação atualizada. Todas as respostas privadas usam `no-store`.

Cada item expõe somente identidade, vínculo de reenvio, versão opaca,
origem, dispositivo, destinatário, conteúdo recebido/enviado, datas, resultado,
falha sanitizada e capacidade de reenvio. Payloads internos, resultados HTTP,
tokens de seleção, segredos, chaves privadas e URLs de armazenamento não são
DTOs públicos. Conteúdo truncado é indicado; conteúdo exibido é escapado.

## Reenvio individual

`POST /{effect_id}/resend` recebe exclusivamente `idempotency_key` (UUID) e
`expected_version`. Destinatário, conteúdo, dispositivo e arquivo vêm do
snapshot persistido. `202` significa nova tentativa registrada, ainda sem
promessa de envio. `GET /resend-requests/{idempotency_key}` consulta a mesma
operação após perda de resposta, sem criar outro envio.

O servidor revalida owner, nicho, empresa ativa, geração/revisão do dispositivo,
política, regra e integridade dos ativos. A avaliação de elegibilidade é
compartilhada entre leitura e comando, repetida sob lock no comando. Mesmo
pedido/chave retorna o mesmo efeito; outra chave para a mesma tentativa não
cria uma bifurcação. A autorização imediatamente antes do transporte continua
obrigatória, mesmo após a admissão do reenvio.

São candidatos somente resultados comprovadamente não enviados e cujo motivo
permita nova tentativa. `uncertain`, resultado com ID de provedor, revisão
revogada, destinatário inválido e motivo legado sem classificação segura
bloqueiam reenvio. Falha de conexão só é reenviável quando ocorreu antes do
relay, não pela palavra "offline" em um texto de erro.

O efeito original permanece terminal. O novo efeito copia a ação já compilada
e recebe nova identidade e registro de auditoria. Chatbot ganha um run de ação
única; não recompila regra, não executa novamente ASR/HTTP nem repete uma
sequência inteira. Listas/carrosséis recebem novos vínculos de seleção a
partir das definições originais. Mídia precisa continuar disponível e íntegra;
imagens já normalizadas de carrossel são reutilizadas de forma privada, sem
buscar novamente uma URL que pode ter mudado.

Se A foi aceita, B não enviada e C cancelada por depender de B, reenviar B
cria B2, mantém A/B/C e não libera C automaticamente. Depois de B2 aceita, C
pode se tornar candidata a um reenvio individual. Uma predecessora incerta
nunca libera a seguinte. Cooldowns não ganham um bypass: eventual reserva
reaproveitada precisa pertencer à mesma mensagem lógica não enviada.

## Validação e publicação

Fixtures exclusivamente sintéticas, banco descartável com autenticação/RLS
reais, transportes falsos e rede bloqueada fora de loopback. Cobertura de
filtros/contagens, fuso, paginação, leitura sem efeitos, isolamento, concorrência,
resposta perdida, sequência parcial e revogação; UI no Chrome oficial e WebKit.
Migração aditiva 0065 somente após backup privado verificado, contrato 0064
válido e barreiras de schema da API/worker. Os resultados e limites da
qualificação serão registrados em `sent-messages-validation.md` antes do merge.
