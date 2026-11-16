# Recepcionista WhatsApp: contexto e escolhas verificadas

## Caminho único de agenda

A entrada Baileys continua autenticada por empresa, canal, proprietário do socket,
fence e contato. O processamento usa `run_locked`, o registro durável de ingresso
e a revisão atual da conversa. O modelo interpreta uma mensagem; não recebe
permissões de escrita nem decide se uma operação aconteceu.

`qr_ai` usa `booking_service` para disponibilidade, hold, confirmação e remarcação.
São as mesmas regras e transações da agenda pública. Somente um resultado real
do domínio pode gerar o recibo de uma operação. Protocolo identifica a reserva;
não autentica o cliente. Não há nova agenda, banco ou migração nesta entrega.

## Contexto e fatos do cliente

Uma saudação é curta e não despeja o catálogo. O nome vem do cadastro do contato
da mesma empresa ou de declaração explícita. Declarações sobre um filho, familiar
ou terceiro não substituem o nome do titular. O modelo não recebe documentos,
data de nascimento ou notas clínicas como memória de atendimento.

O Redis retém até oito mensagens recentes e fatos pequenos da tarefa concluída,
sem hold, snapshot ou consentimento anterior. Esses fatos não provam o estado
atual da agenda. Histórico e pedidos para repetir serviços usam consultas atuais,
com filtro de empresa e telefone; somente o estado `completed` identifica um
atendimento concluído. Um horário que passou não é automaticamente concluído.

## Catálogo e interpretação

O catálogo determinístico suporta até 500 serviços ativos de agenda (`base` e
`combo`). A descoberta oferece páginas de seis serviços, busca por nome e filtro
por categoria cadastrada. Não cria categorias ou preços. Catálogos acima desse
limite são informados como extensos e exigem ajuda da equipe para os demais itens.

Somente até 60 candidatos relevantes vão ao modelo, mantendo as escolhas atuais
e os nomes pesquisados. A validação determinística mantém o catálogo completo e
sua revisão estável. Isso evita que um serviço posterior ao antigo limite de 60
desapareça da conversa ou que toda mensagem leve centenas de itens ao provedor.

## Listas, botões e links

A negociação é explícita: o aviso de ingresso anuncia `assistant_interactions: 1`.
Só então a bridge inclui `capabilities` e, quando há clique, `selection`.
Uma API ou bridge antiga conserva o contrato de texto.

A apresentação é produzida pelo backend, com `answer` textual e uma ação tipada:
`list`, `reply_buttons` ou `url_button`. O modelo não escolhe tokens, destinatário,
URL, comandos de protocolo ou ações. O builder rejeita campos desconhecidos.

Cada escolha contém um token aleatório vinculado à empresa, canal, contato,
proprietário, fence, sessão, revisão do controle humano, estado e prazo. O ACK
autenticado registra o ID da mensagem aceita pelo provedor; um clique precisa
citar esse ID. O ACK é idempotente e não renova a validade. Sob a trava da
conversa, a escolha é consumida antes de retornar ao motor de atendimento.

Uma nova pergunta, correção, mudança de catálogo, expiração ou mudança de
responsabilidade impede o uso da confirmação antiga. A confirmação ainda exige
o resumo completo vigente, outro ingresso identificado e sucesso do domínio.
Profissionais homônimos são selecionados por ID, sem depender do nome exibido.

Links de agendamento são emitidos pelas capacidades de acesso existentes, com
filtro do proprietário pelo telefone. A exceção de fragmento secreto existe
somente no builder da assistente, para o propósito `appointment`, origem própria
HTTPS e caminhos de agendamento permitidos. O validador genérico de links dos
dispositivos de assinaturas permanece inalterado. Não há busca remota da URL.

O protocolo exato fica no cache cifrado antes do relay. Depois de envio aceito
ou incerto, não há uma segunda mensagem automática para tentar outro formato.
O ACK perdido permite reconciliar apenas o ACK, nunca repetir o envio.

## Compatibilidade e ativação

O modo padrão é texto. O nativo exige `QR_ASSISTANT_NATIVE_SCOPES` na bridge,
com entradas exatas `tenant_uuid:channel_uuid:telefone`, separadas por vírgulas.
Não usar curingas, destinos de clientes ou escopos de campanhas para o piloto.
Ativar inicialmente somente o contato autorizado e a empresa de demonstração.

O texto alternativo permite resposta natural e opções numeradas. A conversão de
uma opção numerada em token exige citação da mensagem aceita; um rótulo solto não
é uma prova de seleção. As regras existentes para respostas naturais continuam
válidas. Exibição e clique reais precisam de prova no WhatsApp; construir proto
ou receber ACK não comprova renderização em um telefone físico.

O runtime de dispositivos de assinaturas conserva sua autorização separada.
Não se habilitam dois produtores de negócio no mesmo socket por implicação desta
entrega. Nichos de agenda usam os serviços, capacidades e perfil publicados da
empresa; o runtime comercial existente mantém seus próprios fluxos.

## Áudio

A transcrição compartilhada valida os segmentos `verbose_json` completos.
Silêncio provável, baixa confiança, repetição anômala, segmentos ausentes ou
texto incompleto pedem esclarecimento. Não se executa um trecho sobrevivente de
uma mensagem parcialmente compreendida. Os limites são política conservadora
da aplicação, sem garantia universal de reconhecimento.

Os limites de tamanho, duração, consentimento, quota e decodificação de cada
canal permanecem distintos. A assistente de agenda mantém 2 MB e 120 segundos;
dispositivos comerciais mantêm seus limites próprios. Arquivos e respostas
temporárias são apagados ou zerados pelo fluxo existente, sem logs de conteúdo.

## Publicação, ensaio e reversão

1. Congelar o código e executar a seleção dedicada de Python, PostgreSQL, Redis
   e Node. Registrar SHA, hashes, JUnit e auditoria de rede.
2. Publicar API e worker; conferir saúde e compatibilidade antes da bridge.
3. Preservar o volume e as sessões da bridge. Não trocar SDK, chaves, diretórios
   de autenticação ou material Signal; não desvincular dispositivos.
4. Conferir reconexão normal e ausência de ingresso bloqueado. Ativar o escopo
   nativo exato somente para o piloto autorizado.
5. Ensaiar saudação, catálogo, preço, agenda, confirmação, alteração, cancelamento,
   retorno, áudio e clique antigo. Conferir a reserva na agenda do painel.
6. Reverter os registros criados exclusivamente pelo piloto, mantendo auditoria
   e evidência técnica. Não alterar reservas anteriores ou de terceiros.

Em regressão de apresentação, remover somente o novo escopo nativo retorna ao
texto. Em regressão de aplicação, republicar a revisão anterior sem apagar Redis,
volume, sessões ou dados. Uma API anterior não aceita os novos ACKs: a bridge
mantém o envio já aceito e recusa a ação antiga, sem reenviar a mensagem.

## Limites de evidência

Os testes isolados não são prova de liquidação financeira, de dispositivos
físicos Android/iPhone, de sotaques humanos variados ou de capacidade sustentada.
A matriz distingue narrativas, cenários de banco, contratos de transporte,
transcrições simuladas, arquivos realmente decodificados e ensaios ao vivo.
