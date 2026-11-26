# Troca de senha do administrador
O administrador autenticado abre Administração de clientes > Alterar senha,
informa a senha atual, a nova senha e a confirmação. A operação é exclusivamente
self-service, protegida por sessão verificada, CSRF, limitação de tentativas,
checagem da senha anterior e controle de concorrência. Nenhuma credencial real
é incluída na publicação, nenhum administrador novo é criado e nenhuma senha
é alterada durante deploy. Clientes continuam usando a funcionalidade existente.

Para o administrador nativo, a senha pessoal Argon2id é gravada em uma tabela
dedicada; auth_version é incrementado na mesma transação. A identidade e o
digest de infraestrutura usados pelo worker permanecem intactos. Uma vez
definida a senha pessoal, login e autorização de proprietário não aceitam mais
a senha antiga do ambiente. Não reverter para imagens anteriores que ignoram
essa tabela, nem remover a tabela ou voltar a revisão do banco: a reversão
poderia reabilitar a credencial anterior. Recuperação operacional deve ser
tratada por procedimento autorizado, nunca por um endpoint de bypass.
Antes de publicar: confirmar o digest atual, executar testes com PostgreSQL 16,
backup privado verificado e migração aditiva. Testar que o login real prévio
permanece válido antes da primeira alteração. A nova senha só será escolhida
pelo administrador dentro do painel. Não registrar corpo de requisição,
senhas ou hashes em logs. A migração 0099 é forward-only; releases seguintes
devem usar esta revisão como base e preservar o módulo de autenticação.
