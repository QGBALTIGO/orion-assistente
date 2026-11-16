# Transcrição de Áudio — observação da etapa 12

Data: 05/11/2026, após publicação e conferência ao vivo da etapa 11.
Referência: [Transcrição de Áudio do BotBot](https://botbot.chat/user/credits?channel=whatsapp),
no Chrome conectado e autorizado. A instrução vigente é continuar pelos
módulos acessíveis sem comprar complementos, registrando as pendências.

## Estado

**Carteira e recargas pendentes; sem nova implementação ou publicação nesta
etapa.** A transcrição, suas configurações e suas métricas já existentes no
Orion são descritas abaixo. Não são apresentadas como uma carteira financeira
equivalente à referência.

## Superfície observada

A página permite ler um saldo positivo em horas, minutos e segundos,
identificado como tempo restante de transcrição compartilhado na conta.
O saldo privado da conta de referência não é reproduzido neste registro.

Há três ofertas de compra única:

| Duração anunciada | Preço exibido |
|---|---:|
| 3 horas | R$ 25,00 |
| 7 horas | R$ 50,00 |
| 8 horas | R$ 75,00 |

Cada oferta possui Comprar Agora. A inspeção do DOM desses controles
identificou formulários com método POST para `/user/addons/{addon_id}/purchase`,
sem link de consulta nem sinal DOM de uma modal de prévia. Esses botões não
foram acionados: a ação anunciada é uma compra, incompatível com a instrução
de não pagar. Nenhum pedido, checkout ou pagamento foi criado.

Não há formulário de envio de arquivo, transcrição manual, histórico de
compras, extrato de débito, controle de validade ou ajuste de saldo nessa
superfície. O controle flutuante abre atendimento; não enviamos mensagem a
terceiros nem solicitamos liberação ao suporte.

As fontes públicas oficiais consultadas foram a
[página inicial em português](https://botbot.chat/?lang=pt), a
[página de planos](https://botbot.chat/pricing) e os
[termos do serviço](https://botbot.chat/terms-of-service.html?lang=pt).
Elas anunciam franquias mensais conforme o plano e condições de cobrança,
mas não esclarecem, para essa carteira, arredondamento por áudio, cobrança
de falhas, validade das recargas, prioridade entre franquia e recarga,
acúmulo mensal ou reconciliação de pagamentos. Não há evidência observada
para preencher essas regras como se fossem comportamento do BotBot.

## O que o Orion já tem

O caminho próprio de assinaturas usa `app/subscription_device_audio.py` e
`app/subscription_devices.py`, com a configuração Transcription em
`app/subscription_device_schemas.py`. O cartão de Dispositivos já permite
abrir essa configuração. O fluxo recebe o áudio do canal conectado, valida
arquivo e duração antes de chamar o provedor e revalida o contexto depois.
Não se trata de uma ferramenta pública de upload manual.

O controle de consumo técnico usa `subscription_device_audio_usage`, a
reserva registrada no efeito e os limites de ambiente por empresa e da
plataforma. É uma cota diária UTC para controlar o provedor; não tem preço,
recarga comprada, validade comercial nem equivalência com dinheiro.

`app/subscription_sent_messages.py::transcription_metrics` e o módulo de
Mensagens Enviadas já apresentam duração conhecida total/mensal, registros
históricos sem duração e a cota diária. Esses dados não devem ser renomeados
para sugerir crédito comprado ou saldo acumulado de uma conta.

## Pagamentos existentes e diferença de contrato

O Pix nativo de `app/static/orion-plans.js`, `app/prepaid_pix_api.py` e
`app/prepaid_pix_reconcile.py` vende acesso aos planos por 30 dias. O servidor
aceita os códigos existentes do catálogo e o reconciliador concede prazo
de acesso; não concede segundos de áudio. As rotas de plano também estão
fora da superfície do nicho de assinaturas.

O modal, o QR, o Pix copia e cola, o acompanhamento e a validação de oferta
podem orientar uma implementação futura, mas utilizar esse endpoint como
recarga concederia o produto errado. Não foi ampliado `plan.manage`,
reutilizada cobrança de plano para áudio ou criada oferta comercial a partir
dos preços de outro serviço.

## Pendência e continuidade

Faltam um contrato comprovado de saldo/débito/validade e um contrato próprio
de recarga no Orion, com identidade de oferta e reconciliação adequadas à
unidade de segundos. O preço observado sozinho não define essas regras.
Os comportamentos financeiros após Comprar Agora não foram exercitados.

O mapeamento é somente leitura, sem mudança de código, configuração de
transcrição, cota, preço, produto ou pagamento. Não requer reexecutar as
suítes existentes para afirmar uma implementação que não ocorreu.

Esta pendência não interrompe a lista: a autorização do usuário permite
seguir automaticamente para Aquecimento, etapa 13. Não se declara a carteira
de transcrição concluída ou equivalente ao BotBot.
