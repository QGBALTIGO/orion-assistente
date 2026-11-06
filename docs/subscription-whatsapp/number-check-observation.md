# Etapa 18 — Verificador de números WhatsApp (revisão preliminar)

## Origem e limite de comparação

Solicitação do proprietário: adaptar as funções do BotBot ao nicho de serviços de assinaturas do Orion, preservando design, controle multiempresa e zero compras de complementos. A referência é https://botbot.chat/user/tools/number-checker, mas o formulário autenticado, limites e respostas do BotBot **não foram observados nesta revisão**; não há alegação de equivalência 100%.

A etapa 17 está publicada (PR 272); a PR 273 fez alterações posteriores à ponte e envio e **deve ser preservada**. Esta etapa foi criada a partir do merge 566875d e é apenas candidata de revisão. Não houve envio de WhatsApp nem consulta real a números.

## Contrato provisório seguro

- Exclusivo da empresa **ativa** de serviços de assinaturas, papel proprietário, controle `number_check.manage`; nenhum dos demais nichos recebe essa permissão.
- Reaproveita o dispositivo e socket Baileys já ligados ao tenant/owner: **sem nova instância, novo vínculo QR, login ou envio**.
- O usuário informa até 5 números E.164 únicos por lote (com `+` e DDI) e confirma autorização explícita. Sem importação de contatos, busca em massa ou enumeração de grupo.
- A API reserva uma quota durável **atômica no Redis** de até 30 números por hora, por empresa/dispositivo, que continua valendo após reinício da ponte ou da API. O Redis armazena apenas os IDs da empresa/dispositivo e contador (nunca números consultados); se estiver indisponível, a verificação não acontece. A ponte mantém um segundo limite conservador em memória (30 por sessão/hora), atraso mínimo de 800 ms entre consultas, 15 s de timeout e proibição de concorrência. Uma falha pode consumir quota, sem reenviar a consulta.
- Resultado só classifica **cadastrado** ou **não cadastrado** quando o Baileys devolve um `jid` PN exatamente igual ao número informado com `exists` booleano. Respostas ausentes, LID, incongruentes ou malformadas viram **não confirmado**.
- Valida geração, versão de configurações, sessão, identidade/lease e autorização imediatamente antes e após consultar. Não mantém transação PostgreSQL aberta durante I/O externo.
- Não salva a lista nem cria histórico de números verificados. O front apenas mostra a resposta na página. Sem mensagens, convites ou reconexão.
- Todos os erros ao cliente são limitados a mensagens genéricas e sem dados internos.

## Critérios de aprovação pendentes

1. Testes de validação, concorrência, sessão revogada, PN/LID e limites, rodados em CI no mesmo código.
2. Regressões da ponte e dos planos anteriores, além de testes da tela em Chrome/WebKit com fixture sintética.
3. Validar a quota durável no Redis de produção e as limitações do provedor antes de publicar.
4. Comparação da tela acessível do BotBot (sem compra) e conferência da produção publicada.
5. Somente após testes e auditoria, considerar merge/publicação. Não usar números reais sem escopo e autorização específicos.

A futura etapa 19 (Link e QR) permanece separada e não foi incluída aqui.
