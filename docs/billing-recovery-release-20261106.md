# Publicação de pagamentos e recuperação — 6 de novembro de 2026

Este procedimento publica código e esquema de recuperação. Não autoriza criar,
cancelar ou reembolsar cobranças reais, reenviar recibos de produção ou alterar
planos de clientes. Os testes usam exclusivamente empresas e pagamentos sintéticos.

## Esquema e preservação

A revisão `0073_cakto_recovery` sucede exatamente `0072_subscription_group_add`.
Ela acrescenta a origem de recibos (`webhook_hmac` ou `provider_read`), cursores
de consulta do provedor e o par opcional de QR Pix criptografado/data de expiração.
Recibos anteriores recebem a origem histórica `webhook_hmac`; os novos campos de
consulta e retomada ficam nulos. A migração não altera valores, ofertas, direitos,
assinaturas, estados financeiros ou credenciais existentes.

O QR persistido usa a chave Fernet já exigida pela aplicação. Não deve ser copiado
para logs, relatórios ou artefatos. O banco exige que criptograma e expiração sejam
ambos nulos ou ambos preenchidos, com tamanho limitado. A aplicação valida o
conteúdo e o vínculo com o pagamento antes de apresentar o QR ao proprietário.

## Caminho de publicação

O entrypoint de predeploy é `python -m scripts.predeploy_one_click_links`, declarado
também em `railway.json`. Para bancos em 0072/0073, ele:

1. Mantém a conferência das versões fixadas e da identidade administrativa.
2. Verifica os contratos anteriores com consultas somente de leitura. Não chama
   novamente os migradores antigos nem o provisionamento de catálogo Cakto.
3. Chama `scripts.backup_and_migrate_cakto_recovery`. Origem desconhecida é recusada.
   Em 0072, o processo obtém um lock de publicação, confirma o esquema de origem,
   gera backup privado com proprietários preservados e exige checksum remoto
   verificado. Reconfere revisão e contrato depois do backup e só então migra.
4. Exige a revisão e os contratos finais, inclusive permissões e RLS anteriores.
5. Executa a sonda Cakto de leitura. Se `ORION_PIX_PREPAID_ENABLED=1`, executa ainda
   a sonda Pix, que exige chave Fernet válida e catálogo/produto/webhook verificados
   por GET. Resultado falso, resposta inválida ou exceção bloqueiam a publicação.

O caminho revisado para bancos 0057–0071 mantém sua sequência histórica e depois
passa pelo mesmo gate 0072→0073. O caminho atual 0072/0073 ignora explicitamente
`ORION_PIX_PROVISION_ONCE`, mesmo se essa antiga variável estiver presente.

Uma falha do GET após a migração pode deixar o banco corretamente em 0073 enquanto
o novo processo permanece impedido de servir. Isso não equivale a migração
revertida nem a pagamento confirmado. Investigar a sonda antes de tentar novamente.

## Inicialização e recuperação

API e worker aguardam a barreira 0073 usando `app_runtime` autenticado, restrito e
consultas de catálogo somente de leitura. O worker não recebe o marcador de pronto
se qualquer barreira anterior falhar. A API descarta credenciais de migração antes
de iniciar seu processo de atendimento; migrar nunca é responsabilidade do runtime.

Recuperação por GET do provedor é distinta de criação por POST. Uma operação cuja
resposta de criação ficou incerta deve conservar seu estado para reconciliação;
retomar uma tela ou executar um poll não deve criar outra cobrança. O vínculo
histórico verificado, o proprietário atual, o canal do pagamento e os dados
originais precisam continuar válidos depois das esperas de rede. Um recibo
conflitante ou superado não pode ser promovido novamente por um worker atrasado.

## Falha e rollback

- Backup inválido ou origem não revisada: nenhuma DDL é executada.
- Revisão mudou durante o backup: não sobrescrever; aceitar somente 0073 já
  verificada, ou interromper o processo.
- Banco em 0073: reinicialização verifica os contratos sem novo backup/DDL.
- Não executar downgrade: recibos e proveniência financeira são duráveis.
- O predeploy antigo conhece somente até 0072 e recusa 0073. Rollback exige uma
  imagem com predeploy compatível com o esquema futuro, nunca retornar o banco
  para 0072 ou ampliar permissões para contornar uma barreira.

## Qualificação e limites

O workflow `Billing reliability and recovery` usa PostgreSQL 16/Redis descartáveis,
papéis separados e rede externa bloqueada durante os testes. A prova de concessão
inicial de plano roda antes das demais fixtures. O gate inclui unidades financeiras
e de publicação, lifecycle/concorrência/RLS, migração povoada 0072→0073 e contratos
DOM. Os globs Cakto/Pix incluem os novos testes de recuperação.

A prova de migração cria outro banco aleatório em loopback; nunca faz downgrade
no banco compartilhado dos testes. Ela compara todos os campos anteriores de
treze tabelas, donos, privilégios e RLS; verifica a barreira falsa em 0072 e verdadeira
em 0073; mantém os estados financeiros anteriores e recusa deriva de catálogo.
O bootstrap inicial 0050 é aplicado legitimamente apenas nessa fixture sintética,
antes de povoar os pagamentos: migrations isoladas não criam o marcador de
concessões iniciais. Os vinte verificadores anteriores são executados de verdade
antes/depois da migração, sem dispensar esse pré-requisito de publicação.

DOM, ASGI, provedor simulado e concorrência em PostgreSQL não demonstram liquidação
real pela Cakto. O aceite de uma API ou a conclusão normal de um cron não provam
que um cliente pagou ou recebeu o direito correto. A liberação real deve apresentar
seu próprio recibo, revisão de código, migração, deployment e evidência financeira.

Na observação passiva de 24 horas encerrada em 06/11 às 11:40:47 UTC, os três
serviços observados estavam em `566875d`, sem drift identificado. Foram observados
14 snapshots de startup e 144 inícios/144 conclusões normais do scan Cakto. Cada
snapshot mostrava dois recibos `failed`, zero `verified` e zero `manual_review`.
São snapshots do mesmo total, não 28 falhas distintas; a origem e a idade desses
dois recibos não foram estabelecidas. Esses dados não qualificam entrega de um
evento de pagamento aprovado. Nenhum conteúdo de cliente ou identificador de
pagamento foi incluído no recibo sanitizado.
