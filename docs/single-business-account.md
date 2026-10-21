# Uma empresa por conta

O painel usa uma empresa fixa por conta. A existência de vários clientes na
plataforma não oferece uma troca de empresa para a conta autenticada.

## Contexto do painel

`GET /api/v1/account/businesses` é a fonte comum de contexto para o painel,
independentemente dos flags de perfis e onboarding. A resposta contém zero ou
uma empresa. Uma conta pessoal com mais de um vínculo ativo recebe HTTP 409.
O parâmetro de URL `empresa` não escolhe nem autoriza uma empresa.

Os nomes aparecem como texto. Não há seletor de empresa ou ação para cadastrar
outra empresa. As requisições de dados da empresa e do conector legado aguardam
a resolução do contexto. Falhas apresentam ações para verificar novamente ou
entrar com outra conta, sem carregar os dados de um cadastro arbitrário.

Uma conta sem empresa pode fazer o primeiro cadastro quando o onboarding o
permite. A recuperação de uma resposta perdida revalida o vínculo antes de
consultar o cadastro persistido, mantendo a chave de idempotência original.

## Garantia nas operações

Criação, aceite e aprovação de convite, reativação e atribuição administrativa
serializam suas verificações por conta com o mesmo lock transacional PostgreSQL:
`account-business:<account_id>`. Ele permanece retido até commit ou rollback.
Todos os escritores de `tenant_memberships` da aplicação usam esse contrato.

Uma conta pode ter somente um vínculo ativo, seja de proprietário, editor ou
leitor. Uma chave de idempotência diferente não autoriza cadastrar outra
empresa. O histórico de criação ou de propriedade também impede que revogar
o acesso libere um segundo cadastro. Repetir a mesma operação confirmada
continua recuperando seu resultado.

O onboarding e a autenticação de rotas com empresa detectam vínculos legados
múltiplos. Esses registros são preservados e recebem erro de conflito; o
suporte pode regularizá-los por uma alteração explícita de acesso. Nenhuma
migration ou limpeza automática de dados é incluída nesta correção.

## Acesso administrativo

Os operadores da plataforma mantêm as permissões internas de suporte. Quando
um operador tem um vínculo pessoal, o painel usa esse vínculo. Quando não tem,
`PANEL_OPERATOR_TENANT_ID` define o UUID existente que seu painel exibe. Sem essa
variável, só é possível resolver o contexto se existir exatamente uma empresa
na plataforma. Configuração inválida, inexistente ou ambígua resulta em HTTP 409.

A identidade técnica `legacy_admin` não pode cadastrar uma empresa pessoal.
Contas pessoais continuam sendo verificadas por identidade e vínculo reais,
sem herdar a empresa configurada para o operador global.

Para o acesso administrativo mostrado nesta revisão, foi escolhida
**Barbearia Orion — DEMONSTRAÇÃO FICTÍCIA**, identificada pelo seed existente:
`82552b25-d0c8-5338-8c42-49f11de59e45`. Esse UUID é uma configuração de deploy;
não existe valor fixo dessa empresa no código da aplicação.

## Verificação

A cobertura inclui concorrência entre criações e atribuições, aprovação de
convites e reativação em empresas distintas, replay idempotente, acesso
revogado, contexto administrativo, identidade técnica e conflitos legados.
Os testes HTTP também verificam que a consulta otimizada de autenticação
continua fazendo uma única leitura de conta e escopo.

Os testes DOM usam os controladores e HTML reais. Cobrem a ausência dos
seletores, parâmetros de URL estrangeiros, espera pelo contexto, erro e retry,
logout durante uma consulta, respostas obsoletas, retorno do Google e
recuperação do primeiro cadastro com resposta perdida. As jornadas E2E
existentes foram ajustadas à nova regra.

Na validação local de 21/10/2026 passaram 197 testes Python dos fluxos afetados
com as dependências de produção, PostgreSQL até a migration `0030` e Redis
descartáveis; 233 testes dos contratos de geração e rotas da fachada; e 345
testes DOM da interface. Nenhum desses recortes teve falha ou skip. As jornadas
E2E foram ajustadas, mas não executadas nesta verificação local.

A correção incorpora a base de código já usada pelo Railway, preservando
Central de atendimento, comunicação e canais. A correção anterior do ícone de
conversas acompanha os mesmos arquivos. Preparar código e configuração não
equivale a executar o deploy; a publicação continua sendo uma ação separada.
