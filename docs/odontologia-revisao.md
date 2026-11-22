# Revisão funcional da Odontologia

A referência é a conta autenticada do Simples Dental. A implementação usa o código, a navegação e os componentes do Orion. A base de validação começa com PostgreSQL vazio, migrações do Orion e dados sintéticos. O acesso clínico continua separado da configuração administrativa.

| Área | O que foi identificado na referência | Situação no Orion desta revisão |
| --- | --- | --- |
| Agenda | Calendário por profissional, navegação por data, confirmação e situação das consultas | Agenda Orion existente. A revisão das visões, confirmações e encaixes de odontologia ainda está pendente |
| Pacientes | Lista, cadastro, ficha e exportação. A interface exibiu exportação indisponível para a seleção consultada | Cadastro administrativo e prontuário existentes. A cobertura da ficha inteira ainda está pendente |
| Inteligência | Indicadores, relatórios, secretária, ortodontia e tarefas | Módulo de indicadores existente. As demais funções foram identificadas, com fluxos pendentes de auditoria |
| Vendas | Oportunidades, busca, filtros e criação | Fluxo pendente |
| Financeiro | Fluxo de caixa, fechamento, transações, nota fiscal, comissões e carteira | Recursos do Orion existentes. Comparação de fechamento, transações e integrações pendente |
| Simples Pay e Loja | Módulos visíveis na navegação | Fluxos transacionais pendentes |
| Próteses | Criação, filtro e acompanhamento em etapas até a conclusão | Registro clínico existente. Quadro de operação pendente |
| Marketing | Indicações, comunicação, site e link de agendamento | Site e agenda pública existentes. Paridade dos fluxos pendente |
| Estoque | Entrada, saldo, histórico de retiradas e exportação | Movimentação clínica existente. Experiência e exportação pendentes |
| Ajustes | Clínica, equipe, planos, anamnese, contrato, categorias, contas, cadeiras, comunicação e integrações | Cadeiras implementado e testado. Editor de modelos de anamnese implementado e testado. Demais ajustes pendentes |

## Cadeiras

A tela de Configurações consulta o mesmo cadastro de consultórios usado pela agenda clínica. Ela permite cadastro, edição, filtro por situação, inativação e reativação. A inativação exige confirmação e o servidor recusa a operação quando há uma consulta em aberto. Reservas concorrentes e preservação das consultas encerradas foram testadas em PostgreSQL.

## Modelos de anamnese

A tela de Configurações permite cadastrar modelos, adicionar e editar perguntas e alternar a situação de cada pergunta. Gravações concorrentes conferem a versão. O dentista autorizado pode usar os modelos no prontuário. O registro cifrado guarda as perguntas da versão escolhida e as respectivas respostas. Alterar um modelo não modifica registros já gravados.

## Publicação

O código fica na revisão isolada de Odontologia. O ambiente de produção atual não tem a variável de chave clínica disponível ao serviço. O painel só libera estas configurações com o módulo clínico habilitado pelo servidor. As migrações e os testes desta revisão foram executados em base sintética.
