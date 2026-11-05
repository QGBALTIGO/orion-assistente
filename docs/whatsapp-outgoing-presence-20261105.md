# Presença durante a preparação de mensagens

Os envios de conversa do transporte Baileys compartilham um coordenador por socket e destinatário. Texto, IA, listas, botão com link, imagem, vídeo, sticker, mensagens de atendimento e notificações usam `composing`; áudio usa `recording`. O objetivo é informar que a resposta está sendo preparada. Presença é um recurso de melhor esforço do WhatsApp e não garante exibição em todos os clientes ou prevenção de bloqueios.

## Tempo e isolamento

O alvo visual de texto e conteúdo interativo é proporcional ao conteúdo visível, entre 800 e 3.000 ms. Áudio usa a duração conhecida, entre 800 e 5.000 ms; um áudio de 2,5 segundos tem alvo de 2,5 segundos. Áudios longos não prendem o atendimento pela duração integral. Preparação pela IA, upload e espera na mesma conversa contam para o alvo, sem uma segunda pausa depois desse trabalho. O prazo existente da IA não é ampliado.

Cada conversa conserva a ordem dos seus próprios envios. A espera de um contato não bloqueia outros contatos ou números. Cancelar uma operação que aguarda não libera sua sucessora antes do envio que ainda está ativo. Uma nova preparação de IA não substitui a presença de gravação de um áudio em envio. Não existe timeout cosmético que transforme uma mensagem válida em falha por aguardar essa fila; continuam valendo os prazos e cancelamentos do chamador.

A presença é renovada a cada quatro segundos, por até vinte segundos, e encerrada ao finalizar, cancelar, trocar de sessão ou perder autoridade. Depois de revogação ou perda de propriedade, a sessão antiga não envia nem mesmo um `paused`; a indicação externa expira. Não são alterados estado global online, leitura de mensagens, privacidade ou inscrições de presença. Receipts, sincronização e retransmissões internas do protocolo não recebem atraso artificial.

## Autorização após a espera

O envio revalida a autorização depois da espera, antes da construção/cache do protocolo e antes do relay. A operação, o conteúdo e a identificação de mensagem continuam os mesmos. A preparação de mídia existente pode fazer upload antes dessa última fronteira; isso não constitui autorização para entregar a mensagem após revogação.

Nos dispositivos gerenciados, `send_text` pode revalidar somente o mesmo efeito já em execução, sob o proprietário, fence, revisão, finalidade, validade e consentimento atuais. Não há novo claim, incremento de contador ou renovação do trabalho.

Nos envios do inbox, o servidor registra a autorização limitada no envelope interno da mensagem de saída. A revalidação confere operação, fingerprint, corpo, origem, revisão de controle, proprietário, fence e validade de trinta segundos. Não renova a autorização. Envios manuais revalidam a permissão atual do operador; automações revalidam seu produtor e finalidade. A autorização transacional central de agendamentos conserva a função SQL repetível já existente. Não foi adicionada migração.

O lease anuncia suporte somente com `send_recheck: true`. A bridge nova não envia o novo campo na primeira autorização, nem o usa contra uma API antiga que não anunciou suporte. Nos dois caminhos antigos incompatíveis, a preparação continua sinalizada, mas a espera artificial adicional fica desabilitada até a API atualizada estar disponível. Isso permite atualização dos serviços em momentos distintos.

## Cobertura e evidência

Foram inspecionados os cinco pontos produtivos: envio primário (incluindo fallback), texto gerenciado, ações do Chatbot e envios avulsos/em lote, aviso de conexão primário e aviso de conexão gerenciado. A presença durante a preparação da IA compartilha o mesmo relógio do envio final.

| Qualificação local do conjunto | Resultado |
| --- | --- |
| Node completo comparado à base `8f9dd64` | 1.130 casos: 1.106 aprovados, as mesmas 19 falhas herdadas do fixture de painel, os mesmos cinco skips POSIX no Windows e nenhum cancelamento. Zero novas falhas; 43 casos adicionais, incluindo 11 do botão e 32 de presença |
| Python afetado pela revalidação | 395 casos aprovados em dez módulos, sem falhas, erros ou skips |
| PostgreSQL real de revalidação | 15 casos aprovados, incluindo opt-out, mudança de fence/revisão, prazo, operador revogado, corpo divergente e preservação da operação |

Os conjuntos se sobrepõem. Esses resultados são locais; CI Linux, publicação, recibos e observação visual no cliente devem ser registrados separadamente. A implementação não habilita nenhuma regra ou campanha nova. Consentimento, opt-out, limites de envio e prevenção de duplicatas continuam obrigatórios; os tempos não são usados para contornar as proteções do WhatsApp.
