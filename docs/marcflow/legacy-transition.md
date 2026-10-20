# Transição da sessão QR principal para a central humana

## Autorização e estado de entrada

Em 20/10/2026 o proprietário autorizou concluir a transição e publicar no Railway,
preservando dados, sessões e IA e sem mensagens reais de teste. A consulta atual
confirmou o deploy posterior de `a7199de`: API `97a318ab-7d8e-43a4-a898-b7c53623d868`,
worker `cef72f64-c467-4170-9f11-eebe3075e387` e QR
`75e296ba-26cd-497f-946e-617ebea7cb7e`, todos SUCCESS. O recibo anterior que relata
a confirmação cancelada descreve a primeira tentativa, não a publicação posterior.

## Implementação em qualificação

Uma identidade estável, derivada do estabelecimento configurado no servidor,
representa a sessão principal em `qr_channels`. Não existe segundo dispositivo,
nova credencial de WhatsApp, cópia de diretório, logout, QR ou inscrição de usuário.
O registro exige exatamente um proprietário já existente e ativo; ambiguidade
interrompe a operação antes de gravar. Números autorizados vêm da configuração
existente, com o mesmo limite do cadastro de canais. Não se amplia consentimento.

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
   descartáveis. Nenhuma mensagem externa faz parte dos testes.
2. Publicar primeiro API/worker compatíveis, mantendo INBOX_CONTROL_ENABLED=0 e
   INBOX_SEND_ENABLED=0. O pre-deploy explícito `one_time_legacy_adoption`, fase
   `register`, cria somente a identidade do transporte já existente.
3. Atualizar o bridge com QR_LEGACY_CENTRAL_ENABLED=1 e a mesma chave/volume. O
   adaptador passa a usar os endpoints gerenciados, preservando o contexto da IA.
4. Conferir `/health/central` e o estado privado do adaptador. Essas consultas não
   chamam a IA, consultam contatos no WhatsApp ou enviam mensagens.
5. Na fase `activate`, preservar pausas existentes e inserir o vínculo persistente
   de autoridade. Depois liberar INBOX_CONTROL_ENABLED=1 e INBOX_SEND_ENABLED=1
   na API/worker. META_LIVE_SEND permanece desligado; nenhuma campanha é ativada.
6. Verificar os deployments, saúde, preservação da sessão e ativação efetiva antes
   de declarar a central operacional.

Cada fase usa um UUID v4 próprio em ORION_LEGACY_ADOPTION_RUN_ID e exige
ORION_RUN_LEGACY_ADOPTION=1 e ORION_LEGACY_ADOPTION_PHASE=register/activate.
As credenciais administrativas e as três variáveis da operação são removidas
pelo startup antes de executar a API. Não criar ou copiar segredos para logs.

## Preservação e concorrência

Não há DDL ou nova migration: reaproveitam-se as tabelas da revisão 0028.
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
mesmo em um processo compatível que ainda tenha INBOX_CONTROL_ENABLED=0.
O caminho global de envio é recusado depois da adoção. As opções de novo QR,
iniciar/parar vínculo e habilitar/desabilitar conexão não são usadas nesse alias;
controle da assistente e do atendimento continuam separados da sessão.

## Limites e recibos

Esta transição está em qualificação. Não declarar ativação concluída sem os
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

Qualificação local desta revisão: 429 testes do bridge (425 aprovados, quatro
casos POSIX não aplicáveis ao Windows), 308 testes DOM e dez testes unitários
de transição/histórico aprovados. A CI com PG16 e Redis é obrigatória antes
de publicar; o cenário descartável prepara explicitamente um piloto ativo,
sem alterar a proteção que recusa empresas inativas em produção.

A ativação recusa qualquer saída antiga em queued/sending/uncertain, em qualquer
empresa. A fila e os estados incertos são preservados para reconciliação;
não são liberados como efeito da ativação do worker. A publicação do bridge
desliga a confirmação automática para a própria conta e o modo de teste de
operador, mantendo o atendimento normal da IA e sem mensagem de teste real.
