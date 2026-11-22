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
| Próteses | Criação, filtro e acompanhamento até a conclusão | Lista de trabalhos por etapa, atualização do prontuário, cadastro de laboratórios e vínculo com consultas existentes implementados. Solicitação de coleta pendente |
| Marketing | Indicações, comunicação, site e link de agendamento | Site e agenda pública existentes. Paridade dos fluxos pendente |
| Estoque | Entrada, saldo, histórico de retiradas e exportação | Histórico por insumo com filtro de entradas e saídas e paginação implementado. Exportação pendente |
| Ajustes | Clínica, equipe, planos, anamnese, contrato, categorias, contas, cadeiras, comunicação e integrações | Cadeiras implementado e testado. Editor de modelos de anamnese implementado e testado. Demais ajustes pendentes |

## Cadeiras

A tela de Configurações consulta o mesmo cadastro de consultórios usado pela agenda clínica. Ela permite cadastro, edição, filtro por situação, inativação e reativação. A inativação exige confirmação e o servidor recusa a operação quando há uma consulta em aberto. Reservas concorrentes e preservação das consultas encerradas foram testadas em PostgreSQL.

## Modelos de anamnese

A tela de Configurações permite cadastrar modelos, adicionar e editar perguntas e alternar a situação de cada pergunta. Gravações concorrentes conferem a versão. O dentista autorizado pode usar os modelos no prontuário. O registro cifrado guarda as perguntas da versão escolhida e as respectivas respostas. Alterar um modelo não modifica registros já gravados.

## Histórico do estoque

A equipe com acesso clínico pode consultar entradas e saídas por insumo, com data, quantidade e filtro por tipo. O histórico usa o registro de movimentações do Orion e inclui operações anteriores à revisão. Paginação, retentativas de gravação e isolamento entre clínicas foram testados com PostgreSQL.

## Próteses

Dentistas autorizados acompanham os trabalhos da clínica na aba Próteses. Dá para filtrar por etapa, conferir laboratório e prazo, registrar envio, recebimento, devolução ao laboratório, conclusão ou cancelamento e abrir o prontuário correspondente. O cadastro inicial permanece na ficha do paciente. Cada mudança grava uma versão cifrada e auditada com vínculo à versão anterior. A lista usa a situação mais recente. Os registros anteriores à revisão aparecem na lista com sua etapa original.

Em Próteses, a seção Laboratórios permite cadastrar nome e telefone e editar a situação do cadastro. Laboratórios ativos aparecem como sugestões para novos trabalhos. O prontuário preserva o nome informado quando o trabalho foi registrado.

Na etapa Recebida, o dentista pode vincular ou desvincular uma consulta confirmada da agenda. O painel mostra o cadastro da agenda para conferência e indica quando a correspondência vem do telefone. Cada vínculo é registrado em uma nova versão cifrada do prontuário.

## Publicação

O código fica na revisão isolada de Odontologia. O ambiente de produção atual não tem a variável de chave clínica disponível ao serviço. O painel só libera estas configurações com o módulo clínico habilitado pelo servidor. As migrações e os testes desta revisão foram executados em base sintética.

A verificação `python -m scripts.check_dental_key_history` usa as credenciais de migração do ambiente. Ela registra somente se foi possível verificar o histórico e se uma primeira chave pode ser criada. A presença de prontuários ou exames cifrados exige recuperar a chave que já os protege.

O procedimento `python -m scripts.backup_and_migrate_dental_workflows` prepara o banco para esta revisão. Ele valida o destino e a revisão anterior, exige backup privado verificado e confere o esquema depois de aplicar as migrações. A configuração da chave e a publicação da imagem em produção seguem pendentes.
