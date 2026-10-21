# Administrador existente na central humana

O diagnóstico real de 20/10/2026 confirmou que o QR configurado já aponta para
`orion-demo` (`82552b25-d0c8-5338-8c42-49f11de59e45`). A empresa está ativa,
mas sua IA está desativada, não há proprietário pessoal ativo e o alias da
sessão principal ainda não foi registrado. O administrador legado autentica
como suporte sem `account_id`. Esses são estados observados, não inferências
do seed de demonstração.

O usuário definiu que o atendimento deve usar apenas o administrador. A
compatibilidade desta alteração persiste a identidade desse administrador
existente; não exige outro usuário, e-mail fictício ou nova senha. Consulte o
[recibo de publicação](revisao-manual-20261020/publication-receipt.json) para
distinguir código preparado, testes e etapas realmente aplicadas em produção.

## Identidade e sessões

A migração aditiva `0030_legacy_admin_identity` adiciona o provedor explícito
`legacy_admin` a `panel_accounts`. Apenas esse provedor permite e-mail e hash
de senha nulos, exige papel `admin`, nome de login normalizado e digest da
geração da credencial. Um índice permite no máximo uma identidade desse tipo.
Contas comuns mantêm seus campos obrigatórios, papéis e recuperação existentes.
A migração não cria contas nem memberships, não ativa empresas e não reescreve
cookies, sessões ou senhas.

A senha continua exclusivamente na configuração administrativa atual. O banco
armazena um digest separado por domínio da configuração, sem copiar seu hash
de senha. Não há provisionamento automático em login, consulta ou inicialização.
`PANEL_LEGACY_ADMIN_IDENTITY_ENABLED` é desativada por padrão. Sem identidade
provisionada, o login legado continua funcionando como suporte.

Sessões antigas continuam válidas com os privilégios anteriores. Um **novo
login** com o mesmo administrador e senha pode receber a identidade persistida
depois da configuração; não há elevação silenciosa de uma sessão antiga. Cada
requisição nativa verifica novamente provedor, papel, versão e geração da
credencial. Desativar a opção ou mudar usuário, senha ou versão impede o uso de
uma identidade antiga. O acesso de suporte por token não recebe essa identidade.

O proprietário do QR precisa ter uma membership `owner` ativa na empresa exata.
O papel global `admin` sozinho não permite criar ou administrar canais. O admin
nativo validado e associado pode operar seu canal; contas administrativas de
outro provedor não recebem essa exceção. Pareamento, logout, exclusão ou
desligamento da sessão principal continuam protegidos.

O trabalhador de mensagens também revalida a geração administrativa antes de
qualquer tentativa manual, usando um contexto local à transação. Configuração
ausente, inválida ou alterada recusa o envio antigo. A API e o worker precisam
receber a mesma configuração administrativa, por referências entre serviços;
nenhum segredo deve ser copiado para o relatório ou para o Git. Uma rotação
posterior exige revalidação explícita do vínculo persistido; até lá, o login
legado de suporte continua disponível e a central recusa a identidade antiga.

## Operações protegidas

1. Publicar o código qualificado e executar somente a migração estrutural
   revisada, com `one_time_admin_identity_migration`. O operador verifica o
   esquema exato 0029, sela a migração, cria backup cifrado com leitura de volta,
   restaura e ensaia no banco descartável antes de alterar o original. Compara
   dados anteriores, permissões, catálogo, programas e sequência de ingresso.
   Um alvo 0030 adulterado é recusado pela definição exata do CHECK e da função.
2. Executar `one_time_admin_demo_setup` na fase `plan`. Ela apenas consulta e
   produz o hash do plano, vinculado ao estado do banco, à operação, à geração
   administrativa e à prova da sessão conectada. Não cria conta ou proprietário
   nessa fase.
3. Após aprovação do plano concreto, a fase `apply` persiste **o próprio admin**,
   acrescenta sua membership de proprietário somente na demonstração, muda
   apenas `bot_enabled` dessa empresa de falso para verdadeiro e registra
   auditoria. Exige a demonstração ativa, IA previamente desativada, ausência de
   proprietário e identidade nativa anteriores e alias ainda ausente. Não
   sobrescreve estados existentes. Repete backup, leitura de volta, restauração
   e ensaio antes de gravar, e recusa qualquer divergência do plano aprovado.
   A comparação acompanha a política já usada pela adoção: somente
   `lease_owner`, `lease_expires_at`, `fence` e `updated_at` de `qr_channels`
   podem avançar com o transporte ativo. O setup nunca escreve nesses campos;
   proprietário, revisão, credenciais, allowlist e demais dados continuam
   vinculados ao plano. A sessão principal também mantém sua verificação HMAC.
4. Registrar o alias da mesma sessão, verificar o adapter e adotar a autoridade
   persistente pelos operadores existentes. A adoção aceita os esquemas exatos
   0028, 0029 e 0030 e mantém as guardas de proprietário, empresa, credenciais,
   allowlist, sessão e ausência de envios pendentes ou incertos.
5. Habilitar controle e envio humano somente depois dessas provas. Campanhas,
   Meta e sondagens com mensagens continuam desativados. Não usar mensagens reais
   como teste. O novo login do administrador é necessário para ações auditadas;
   a sessão anterior continua disponível para consulta e administração legada.

As opções globais `QR_AI_ENABLED=1` e `QR_CHANNELS_ENABLED=1` são preservadas.
O setup não desativa as ferramentas do atendimento, não limpa filas e não toca
no volume QR, diretório de autenticação, telefone, chaves, sessões Redis ou
histórico. Ativar a IA da demonstração é uma mudança explícita de estado da
empresa; não deve ser apresentada como se já estivesse ligada antes da tarefa.
Preservar essas opções não garante latência zero durante a migração: os locks
de banco têm limite de espera de 10 segundos e cada instrução SQL tem prazo
de 120 segundos. Operações concorrentes podem aguardar ou fazer a etapa parar.

O processo normal da API remove as credenciais e opções dos operadores de
migração/provisionamento antes de iniciar o servidor público. Após cada etapa,
restaurar o hook normal e limpar as opções de uso único sem reiniciar serviços
que não precisam mudar. A aplicação anterior pode continuar atendendo após uma
falha, mas a migração 0030 pode ter concluído antes de falharem o plano ou a
sondagem do bridge. O banco não tem rollback automático nessa situação. Não
executar downgrade nem restaurar o banco original. Antes de repetir a etapa,
conferir o estado atual: o operador novo aceita `already_applied` somente após
verificar o esquema, o CHECK e a definição selada da função já aplicada.
Falhas não autorizam apagar dados, escolher outro proprietário ou forçar
reconexão.

Durante a fase entre a adoção persistente e a habilitação do controle global,
o ingresso adotado deve ligar o contexto autenticado do canal antes de ler
bloqueio e revisão da conversa. Assim, a própria adoção exige autoridade
persistente mesmo com `INBOX_CONTROL_ENABLED=0`. O contexto e a revisão da IA
devem ser limpos em `finally`, inclusive em pausas, falhas e cancelamento.
Essa ordem não dispensa a verificação de geração, posse e revisão antes das
gravações nem a guarda final de transporte.

Publicar o worker somente depois do recibo de esquema 0030 e do gate
`admin_identity_schema_ready` confirmados. O worker usa a conexão restrita de
runtime e não deve receber a credencial owner nem as opções dos operadores de
migração ou provisionamento.

## Evidências e limites

As suítes novas cobrem autenticação com armazenamento substituído, permissões
QR, interface e PostgreSQL real com destinatários e transportes sintéticos.
Contagens, falhas corrigidas e resultados efetivamente executados estão no
recibo. Testes isolados não comprovam login real do administrador, entrega no
WhatsApp, integração nativa da Meta ou ativação da central em produção.
