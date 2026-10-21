# Transição da sessão QR principal para a central humana

## Autorização e estado atual

Em 20/10/2026 o proprietário autorizou concluir a transição e publicar no Railway,
preservando dados, sessões e IA e sem mensagens reais de teste. As fases C/D/E/F
abaixo continuam pendentes; aprovação de plano ou publicação de código não prova
registro, adoção ou ativação. O estado observado e os recibos mais recentes estão
na [revisão manual](manual-inventory-review.md) e no
[recibo de publicação](revisao-manual-20261020/publication-receipt.json).
O pré-requisito do administrador está descrito em
[identidade nativa do admin](admin-central-identity.md).

O registro histórico da primeira publicação confirmou `a7199de`: API `97a318ab-7d8e-43a4-a898-b7c53623d868`,
worker `cef72f64-c467-4170-9f11-eebe3075e387` e QR
`75e296ba-26cd-497f-946e-617ebea7cb7e`, todos SUCCESS. O recibo anterior que relata
a confirmação cancelada descreve aquela primeira tentativa, não o estado atual.

## Implementação em qualificação

Uma identidade estável, derivada do estabelecimento configurado no servidor,
representa a sessão principal em `qr_channels`. Não existe segundo dispositivo,
nova credencial de WhatsApp, cópia de diretório, logout, QR ou inscrição de usuário.
O registro exige exatamente um proprietário elegível já existente e ativo na
empresa exata; ambiguidade interrompe a operação antes de gravar. No esquema
0030, o admin nativo precisa do provedor `legacy_admin`, configuração validada
e membership `owner` ativa; o papel global `admin` sozinho não basta.
Números autorizados vêm da configuração existente, com o mesmo limite do cadastro
de canais. Não se amplia consentimento.

O adaptador usa o socket, envio confiável, endereçamento verificado, cache cifrado
e histórico locais atuais. O gerenciador existente acrescenta lease/fence,
arbitragem, journal e recibos, sem criar outro Baileys. A fábrica comum recusa
uma identidade principal para impedir abertura acidental de outro socket.
Encerrar o adaptador não encerra nem apaga a sessão principal.

As chaves de contexto, revisão de entrada e idempotência da IA principal conservam
o namespace anterior. Dispositivos adicionais permanecem isolados por canal.
Não são migrados/copiados os dados Redis. A central e as operações de negócio
usam as permissões e a autoridade persistente já existentes.

## Procedimento de publicação

1. Qualificar backend, interface, bridge e o procedimento com PG16/Redis
   descartáveis, incluindo adoção com controle global desligado. O ingresso deve
   ligar o contexto antes de ler a autoridade, e `run_locked` deve consultar a
   autoridade efetiva também nesse intervalo. Nenhuma mensagem externa faz parte
   dos testes. Para o alvo atual, conferir o esquema exato 0030 e o gate
   `admin_identity_schema_ready` antes de iniciar API/worker compatíveis.
2. Conferir o recibo de provisionamento aplicado: admin validado, membership
   `owner` ativa na empresa exata e `bot_enabled=true`. Manter
   `INBOX_TOOLS_ENABLED=1`, `INBOX_CONTROL_ENABLED=0` e `INBOX_SEND_ENABLED=0`
   na API/worker, preservando `QR_AI_ENABLED=1` e `QR_CHANNELS_ENABLED=1`.
3. **C — registro:** com `QR_LEGACY_CENTRAL_ENABLED=1` na API, executar o pre-deploy
   explícito `one_time_legacy_adoption`, fase `register`. Ele cria somente a
   identidade do transporte existente. Conferir seu recibo de backup cifrado,
   leitura de volta, restauração, ensaio e registro antes de avançar.
4. **D — adaptador:** somente depois de alias e bootstrap válidos, atualizar o
   bridge com `QR_LEGACY_CENTRAL_ENABLED=1`, mesma chave, tenant, allowlist,
   volume e diretório de autenticação. Manter `ORION_NOTIFY_SELF_ON_LINK=0`
   e `QR_OPERATOR_TEST_ENABLED=0`. Conferir `/health/central`, estado privado
   pronto/conectado e prova HMAC da mesma sessão. Essas consultas não chamam a
   IA, consultam contatos no WhatsApp ou enviam mensagens. Ligar o adaptador antes
   de sua preparação pode interromper respostas por recusa segura.
5. **E — adoção:** com o runtime corrigido e qualificado, executar `activate` com
   outro UUID e outra prova completa de backup/restauração/ensaio. Preservar
   pausas existentes e inserir o vínculo persistente de autoridade. Qualquer
   backlog global pendente ou incerto bloqueia a etapa; não limpar nem reenviar
   a fila para contornar a guarda. Controle/envio globais continuam em `0`.
6. **F — central humana:** depois dos recibos anteriores e da saúde confirmada,
   liberar `INBOX_CONTROL_ENABLED=1` e `INBOX_SEND_ENABLED=1` na API/worker e
   conferir a configuração efetiva dos processos. O administrador precisa de
   novo login para receber sua identidade persistida; sessões antigas conservam
   seus privilégios. `META_LIVE_SEND` e campanhas permanecem desligados.
7. Verificar deployments, saúde, preservação da sessão, identidade/permissões e
   habilitação efetiva antes de declarar a central operacional. Saúde do
   adaptador não comprova resposta funcional da IA nem login real. Não realizar
   mensagens reais de teste.

Cada execução do operador nas fases C/E usa um UUID v4 próprio em
ORION_LEGACY_ADOPTION_RUN_ID e exige ORION_RUN_LEGACY_ADOPTION=1 e
ORION_LEGACY_ADOPTION_PHASE=register/activate.
O startup `scripts.start_api` remove a credencial owner e as opções dos
operadores antes de executar a API. O worker não recebe essas credenciais nem
opções; sua inicialização ARQ não faz essa limpeza. Após cada fase, restaurar
o hook normal com gate 0030 e sondagens somente de leitura, limpando somente
opções e credenciais de uso único. Não reutilizar a limpeza histórica que
desligava a central antes da adoção: `QR_LEGACY_CENTRAL_ENABLED=1` deve permanecer
na API e no QR após D/E, inclusive com `INBOX_CONTROL_ENABLED=0`. Preservar também
a configuração administrativa de runtime na API/worker. Não criar ou copiar
segredos para logs.

## Recuperação após escrita concluída

Uma falha de probe, inicialização ou deployment posterior não desfaz uma escrita
que já terminou. Se existir recibo completo do operador, conferir primeiro seu
UUID, fase, banco, revisão, backup/restauração/ensaio e resultado, junto ao estado
atual em leitura: identidade e membership do admin, IA da empresa, alias e vínculo
persistente correspondentes. Não tratar a falha posterior como prova de que
nenhuma escrita ocorreu.

Se o setup já tiver concluído, não repetir sua fase `apply`: ela exige ausência
prévia da identidade e do proprietário. Não inventar outro plano, remover o
cadastro nem restaurar/downgradear o banco para permitir repetição. Na adoção,
conferir o registro ou vínculo já existente e seus recibos antes de decidir
qualquer retomada. Recibo ausente ou estado divergente exige parar e esclarecer
o resultado, sem reexecutar automaticamente.

Publicar somente o hook normal depois de verificar o estado gravado, a revisão
compatível do runtime, o gate do esquema 0030 e a saúde/prova da sessão exigidas
pela fase. Limpar apenas as opções de uso único; preservar a autoridade adotada,
as flags de runtime, pausas e slots incertos. Nunca restaurar o backup sobre
registros novos nem forçar reconexão para contornar uma falha posterior.

## Preservação e concorrência

O operador de adoção não executa DDL nem nova migração. Ele aceita somente os
esquemas exatos revisados 0028, 0029 e 0030; o alvo do rollout atual é 0030,
com a migração estrutural e sua barreira verificadas separadamente.
Cada fase exige backup criptografado durável, leitura de volta, restauração e
ensaio em banco isolado. Todos os registros existentes, catálogo, constraints,
permissões e programas são comparados. Somente renovações voláteis de lease dos
canais são excluídas dos hashes de configuração; elas nunca são sobrescritas.
Nome, OID e proprietário do banco temporário são conferidos antes de removê-lo.

Na ativação, locks de conversa só são adquiridos para a transação final, na mesma
ordem usada pela IA; backup e ensaio não mantêm esses locks. Contexto Redis em
modo humano ou pausa operacional persistida origina controle humano, sem prazo
de retomada. Mudança de fonte, configuração ou pausa após o backup recusa a
transação. Não se libera automaticamente um slot ou resultado de envio incerto.

O vínculo persistido é observado em ingresso, revalidação e transporte. Durante
o escoamento dos processos anteriores, a identidade adotada força a arbitragem
mesmo em um processo compatível que ainda tenha INBOX_CONTROL_ENABLED=0,
mantendo `QR_LEGACY_CENTRAL_ENABLED=1` na API e no QR. Nessas condições, o caminho
global de envio é recusado depois da adoção. As opções de novo QR,
iniciar/parar vínculo e habilitar/desabilitar conexão não são usadas nesse alias;
controle da assistente e do atendimento continuam separados da sessão.

## Limites e recibos

As qualificações abaixo são históricas; a árvore do rollout atual precisa de
seu próprio fechamento de evidências. Não declarar ativação concluída sem os
recibos das duas fases e as conferências do Railway. Mídias completas, executor
de grafos, campanhas avançadas, exportações e webhooks mantêm as pendências da
matriz; não são certificados como completos por esta transição.


O proprietário da sessão pode consultar o histórico local preservado, limitado
às últimas cem mensagens do contato autorizado. Essa leitura revalida vínculo,
proprietário e contato antes e depois de consultar o bridge; não importa dados,
marca leitura, envia recibo nem presume entrega. Outros atendentes não recebem
esse acesso adicional ao histórico anterior. A central mantém seu histórico novo
com leitura pessoal e acesso por empresa conforme as permissões vigentes.

A primeira CI da adaptação encontrou fixtures que extraíam o handler antigo sem
fornecer o novo controlador opcional. As fixtures foram atualizadas para o modo
legado desligado, mantendo suas asserções; foi acrescentado um teste do handler
adotado que exige journal antes do processamento e zero chamada ao caminho antigo.

A identidade preservada inclui uma prova HMAC do aparelho principal conectado,
validada antes do backup, antes da escrita e pelo adaptador a cada operação.
O número e a chave privada não são expostos nos recibos. Uma troca de conta
impede a adoção ou o envio pela identidade anterior.

Qualificação local histórica da revisão anterior: 429 testes do bridge (425
aprovados, quatro casos POSIX não aplicáveis ao Windows), 308 testes DOM e dez testes unitários
de transição/histórico aprovados. A CI com PG16 e Redis é obrigatória antes
de publicar; o cenário descartável prepara explicitamente um piloto ativo,
sem alterar a proteção que recusa empresas inativas em produção.

O procedimento atual de ativação recusa qualquer saída antiga em
queued/sending/uncertain, em qualquer empresa. A fila e os estados incertos são
preservados para reconciliação;
não são liberados como efeito da ativação do worker. A publicação do bridge
desliga a confirmação automática para a própria conta e o modo de teste de
operador, mantendo o atendimento normal da IA e sem mensagem de teste real.

## Recibo histórico da tentativa anterior — 20/10/2026

Runtime qualificado naquela tentativa: `7a3dafb906003a38e529b62deec2d139d875e890`.
[CI principal](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39466167900)
e [CI de mídias](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39466167895)
concluídas com sucesso. PostgreSQL/Redis: 4.222 aprovados, um ignorado e 175
avisos; Compose: 4.097 aprovados, 126 ignorados e dez avisos (conjuntos
sobrepostos, não somar). Bridge: 429 aprovados, zero ignorados/falhas no Linux;
DOM: 308 aprovados. Áudio/ASR e imagens reais dos serviços aprovados. Navegador:
seis combinações de tamanho/tema, APIs simuladas e zero envio real.

A chamada `accept_deploy` da fase `register`, às 17:36 UTC, retornou:
**“Cancelled — the user did not approve this action. No changes were made.”**
Apesar da autorização expressa no chat, o controle de aprovação da integração
não efetivou a publicação. Não houve tentativa por CLI ou outro canal para
contornar a recusa. Não há recibo novo de backup/restauração/adoção em produção.
O patch `07d4a48a-3a3a-4f76-9ddc-f0c3d3ccb335` conservava 22 alterações preparadas,
somente API e worker; não inclui troca do bridge, bancos, volumes ou segredos.

A conferência após a recusa confirmou os mesmos deployments de 16:45 UTC,
commit vivo `a7199de33ed265705a2a0116f01cbb43b488bd5c`: API
`97a318ab-7d8e-43a4-a898-b7c53623d868`, worker
`cef72f64-c467-4170-9f11-eebe3075e387`, bridge
`75e296ba-26cd-497f-946e-617ebea7cb7e`. `/health` e `/health/whatsapp` responderam
200. Isso prova disponibilidade da versão anterior, não ativação desta transição.
Dados e sessão não foram modificados por esta tentativa; nenhuma mensagem real
foi enviada como teste. O backup/restore da migração anterior para 0028 já existe
em produção; não deve ser confundido com a nova adoção ainda não aplicada.

Naquela tentativa, continuaram necessárias a aprovação efetiva da primeira fase,
sua conferência, a publicação do adaptador e depois a ativação da autoridade humana. Ao publicar
o bridge, fixar a revisão qualificada e manter o volume/chave/diretório existentes;
ORION_NOTIFY_SELF_ON_LINK=0 e QR_OPERATOR_TEST_ENABLED=0 evitam notificações ou
testes automáticos. Não desabilitar a autoridade persistente nem voltar ao runtime
anterior à adaptação depois da adoção: a reversão deve usar código compatível,
conservando pausas e slots incertos. Nunca restaurar o backup sobre registros novos.

Recibo histórico legível por ferramenta: `legacy-transition-validation.json`.
