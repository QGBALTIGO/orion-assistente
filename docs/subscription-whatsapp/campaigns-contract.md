# Etapa 07 — contrato de Campanhas

Estado: contrato de implementação. Permissão `campaigns.manage`, proprietário
atual, empresa ativa, workspace de assinaturas. Rota `campanhas`. Base pública:
`/api/v1/tenants/{tenant_id}/subscription-whatsapp/campaigns`.

## Função e ciclo de vida

Uma campanha tem título, dispositivos, grupos, texto ou template, início, fuso,
janela diária, dias permitidos e intervalo de 30–60 s. Salvar cria um rascunho;
revisar produz uma prévia persistida; confirmar agenda a audiência e o conteúdo
exatos dessa prévia. GETs, seleção, navegação e recarga nunca admitem envios.

Estados: `draft`, `scheduled`, `running`, `paused`, `completed`, `cancelled`.
Arquivar é exclusão lógica com preservação do histórico e cancelamento de novas
admissões. Pausar interrompe admissões e a autorização final antes do transporte.
Retomar exige gesto explícito, mesmo conteúdo e audiência congelados, sem repetir
destinatários já tentados. Rascunhos podem ser editados; uma campanha agendada ou
pausada só pode voltar à edição antes da primeira admissão. Uma operação já
entregue ao provedor pode terminar após a pausa e deve permanecer registrada.

Destinatários são deduplicados por telefone canônico entre todos os grupos. Cada
um recebe a sequência da campanha uma vez, por um dos dispositivos disponíveis
selecionados; distribuição determinística. Não se transmite a mesma campanha
por todos os dispositivos ao mesmo contato. As partes de um template preservam
a ordem: arquivo, legenda quando separada e mensagem complementar. Não se usam
regras do Chatbot, HTTP externo de respostas, ASR ou envio arbitrário do browser.

A audiência e os dados de personalização são congelados na confirmação. Remover
um contato de todos os grupos de origem, excluir grupo, revogar autorização,
trocar proprietário/nicho, desativar dispositivo ou mudar geração/revisão impede
admissões futuras daquele contexto. Alterar um template impede enviar conteúdo
que já não corresponde à revisão confirmada. Mudança de nome de grupo não troca
o nome do destinatário congelado. Grupos e contatos adicionados depois não entram
automaticamente em campanhas já confirmadas.

## Grupos e autorização de contatos

A gestão mínima de grupos fica dentro de Campanhas. Grupo tem nome e contatos
`{name,phone}`; entrada manual por linhas `Nome;telefone` é local ao formulário.
Telefones aceitam apresentação comum e são normalizados ao domínio E.164 de
Dispositivos (8–15 dígitos, sem JID/LID/extensão). Limites Orion: 5.000 contatos
por grupo, 100 grupos e 10.000 destinatários únicos por campanha. Grupos são
versionados; mutações usam versão esperada e chave idempotente. Criar/importar
grupo não concede autorização de envio.

A confirmação da campanha exige atestado explícito do proprietário de que os
destinatários autorizaram essa comunicação. Essa autorização é registrada com
a audiência exata. Não sobrepõe uma revogação registrada do contato. Pedidos
inequívocos de parar mensagens promocionais ou todas as mensagens, recebidos
pelo dispositivo, registram revogação por empresa+telefone e impedem pendências
e a autorização final. Reutiliza-se o reconhecedor puro de preferências existente;
pedidos de aniversário apenas não são convertidos em bloqueio de campanha.

Um proprietário pode registrar uma nova autorização para contatos selecionados
de um grupo por ação separada, com confirmação, evidência textual limitada e
versões esperadas das revogações. Guarda-se hash da evidência. Isso não retoma
campanhas antigas: elas mantêm a versão de autorização congelada. Nenhum dado
de grupos/contatos de outro nicho é aberto por grants novos.

## API pública e recuperação

Todos os GETs usam `Cache-Control: no-store`; erro e ausência são distintos.
Versões de campanhas/grupos são inteiros crescentes. A revisão para agendar usa
hash SHA-256 opaco de 64 caracteres, vinculado à campanha, ator, conteúdo,
dispositivos, grupos, contatos e versões. Prévia expira em 120 s.

| Método e sufixo | Corpo / resposta |
| --- | --- |
| GET `/` | Pesquisa `q`, estado, página; `{items,total,page,page_size,pages,generated_at,timezone}`. |
| GET `/options` | `{devices,templates,groups,timezones,timezone,limits}`. Nenhuma seleção automática de grupo/dispositivo. |
| GET `/requests/{key}` | Recupera somente a operação autorizada; nenhuma repetição de POST, admissão ou reconciliação de rede. |
| POST `/` | `{idempotency_key,campaign}`; cria rascunho. |
| GET `/{id}` | `{campaign,progress,groups,recipients}` com totais e primeira página. |
| PATCH `/{id}` | `{idempotency_key,expected_version,campaign}`; edição permitida pelo estado. |
| POST `/{id}/review` | `{idempotency_key,expected_version}`; persiste prévia, não cria efeitos. |
| POST `/{id}/confirm` | `{idempotency_key,review_version,confirmation:"AGENDAR",consent_confirmed:true}`; mesma chave da revisão, sem conteúdo/telefone do browser. |
| POST `/{id}/actions` | `{idempotency_key,expected_version,action:"pause"|"resume"|"edit"|"archive"}`. |
| GET `/{id}/recipients` | `status=sent|pending|failed|uncertain|cancelled`, `q`, página; resultados verdadeiros e paginados. |
| GET/POST `/groups` | Lista / `{idempotency_key,name,members}`. |
| GET/PATCH/DELETE `/groups/{id}` | Detalhe / `{idempotency_key,expected_version,name,members}` / `{idempotency_key,expected_version}`. |
| POST `/groups/{id}/permissions` | `{idempotency_key,expected_version,contacts:[{phone,version}],evidence,confirmation:"AUTORIZAR"}`; nova autorização explícita. |

Objeto `campaign` de criação/edição: `{title,device_ids,group_ids,message_type,
text,template_id,start_date,start_time,timezone,window_start,window_end,weekdays,
interval_seconds}`. `message_type=text|template`, campos incompatíveis nulos;
`weekdays` usa domingo=0 até sábado=6. Texto tem até 3.500 unidades UTF-16 e só
substitui `{name}`/`{phone}` uma vez, sem interpretar marcadores dentro dos valores.
Templates preservam os oito tipos e marcadores já aceitos pela biblioteca.

A revisão e a confirmação também verificam incrementalmente o tamanho agregado
dos planos personalizados, com orçamento de 64 MiB. Isso cobre expansão de
marcadores: um texto curto repetindo o nome pode se tornar muito maior para cada
contato. Exceder o orçamento retorna `CAMPAIGN_LIMIT` antes de inserir a audiência,
orientando reduzir o texto ou dividir os destinatários em campanhas menores.

Operações retornam envelope `{request_id,idempotency_key,kind,status,campaign_id,
group_id,result,preview,replayed,created_at,expires_at}`; campos inaplicáveis nulos.
Preview: `{review_version,expires_at,campaign,total,eligible,blocked,duplicates,
groups,devices,message_preview,schedule}`. Conteúdo só permanece no servidor e
memória da tela. History guarda somente tenant, identidade SHA-256 da conta,
chave, ação e id do alvo. Após erro ambíguo, GET recupera a mesma chave; não há
POST automático, nova chave automática ou confirmação automática após recarga.
Um 404 de recuperação não prova que o pedido atrasado não será admitido.

## Horários, fila e transporte

O início é interpretado no fuso selecionado, convertido para UTC e exibido com
seu fuso. Horários locais inexistentes/ambíguos exigem correção explícita.
Pseudozonas `Factory` e `localtime` não são oferecidas. A interface filtra opções
que o navegador não reconhece, sem invalidar os demais dados. Uma zona já salva
permanece exatamente como foi definida; se o navegador não puder formatá-la,
a tela informa explicitamente a apresentação em UTC, sem trocar o agendamento.
Janelas que atravessam meia-noite pertencem ao dia de início da janela. Um dia
sem janela permitida adia a próxima admissão; o reinício não descarrega atrasos
em rajada. O intervalo é mínimo, não garantia de entrega pontual.

A execução conserva no máximo um destinatário de campanha admitido por empresa
até que sua sequência termine. A próxima admissão exige pelo menos seis segundos
após o término anterior da empresa e o intervalo de 30–60 segundos após o término
anterior da mesma campanha. O limite móvel de dez contatos por minuto também é
verificado. O tempo de conclusão é posterior à tentativa do transporte; atrasos
de capacidade, consulta do destinatário ou preparação de mídia não permitem uma
rajada de compensação. Isso pode aumentar o prazo de campanhas concorrentes.

Planejamento durável fica em tabelas próprias. A admissão pode ocorrer no poll
privado já existente do dispositivo conectado, independente de qualquer página
aberta. Não depende de chamadas públicas GET. Uma reserva SQL por empresa mantém
no máximo dez contatos iniciados em qualquer janela móvel de 60 s e o intervalo
mínimo por campanha. Continua respeitando a capacidade de envio da ponte e a
ordem de partes por contato. Efeitos são criados somente perto da janela atual,
com prazo curto, e não são usados como agendamentos de longa duração.

Novo propósito e tipo `campaign_action`, com autorização específica vinculada a
campanha/destinatário/parte. Não se falsifica run de Chatbot ou regra do usuário.
O broker repete owner, nicho, feature, geração, revisão, estado da campanha,
janela, membro dos grupos e revogação antes de claim e imediatamente antes do
relay. A ponte usa o transporte de templates validado e reserva recibo durável
antes do efeito externo. Uma tentativa incerta nunca é repetida automaticamente.
Recuperar recibo grava somente a evidência já obtida, sem novo envio/download.

“Enviado” é aceito pelo transporte, não comprovação de entrega/leitura. O detalhe
mantém estado incerto separado e falha parcial de template visível. A primeira
parte falha/incerta impede as demais. Mensagens Enviadas identifica a origem
Campanha e não oferece reenvio fora da autorização específica da campanha.

## Persistência e verificação

Migração única `0068_subscription_campaigns`, após `0067_subscription_diagnostics`.
Tabelas de grupos, revogações, campanhas, destinatários e pedidos com FKs/uniques,
RLS forçada e grants mínimos ao papel de suporte; o runtime geral não ganha acesso
ao conteúdo privado. API e worker aguardam o catálogo correto. Predeploy verifica
backup anterior à migração; todos os marcadores anteriores continuam válidos.

Verificar contratos puros, PG real (RLS, rollback, concorrência, deduplicação,
pause/opt-out na corrida final, scheduler e recibos), ponte com provedor sintético,
DOM, Chromium e WebKit em celular/tablet/desktop claro/escuro. A integração precisa
preservar PRs 254/256/257 e a árvore efetivamente publicada; PRs abertas não entram na
base sem merge. Publicação e teste ao vivo acontecem antes de observar a etapa 08.
