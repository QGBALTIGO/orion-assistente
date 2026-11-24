# Reconstrução da Orion Odontologia

Levantamento inicial de 24 de novembro de 2026. A matriz registra capacidades, testes e publicação conforme confirmados. A referência autenticada foi consultada pelo navegador, com formulários abertos sem salvar e com a ficha do paciente de teste. As telas foram recriadas com o padrão da Orion.

A recepção opera dentro do painel. O link público é opcional. A consulta interna, o cadastro de pacientes e a configuração do primeiro profissional devem funcionar sem publicação de site. Os registros clínicos e exames exigem a autorização profissional existente.

## Entregas e evidências

A recepção foi publicada em 24 de novembro de 2026. O cadastro de dentistas, o expediente, os procedimentos e a ficha administrativa foram exercitados pelo navegador contra PostgreSQL e Redis em uma clínica sintética. A reserva de 45 minutos apareceu na agenda. Os testes também cobriram remarcação, cancelamento, liberação de cadeira, colisões de profissional e paciente, CPF, telefone compartilhado, versões do cadastro, acesso por recepção e isolamento entre empresas. A migração partiu do banco usado na versão anterior. A verificação do backup privado e do esquema aparece na implantação. [Compilação da recepção](https://github.com/QGBALTIGO/orion-assistente/actions/runs/45461915430).

Na conta da Ana Clara, o menu abriu Profissionais, Pacientes e Agenda. Os formulários foram conferidos sem gravar registros. O site da clínica estava fora do ar e sua prévia autenticada carregou. A clínica pode trabalhar pela Agenda com o site fora do ar.

A configuração do link odontológico passou por simulação de publicação, consulta no celular e retirada do ar. O teste liberou uma avaliação, guardou um tratamento no catálogo interno, bloqueou chamadas diretas para procedimentos excluídos, verificou a confirmação com protocolo e a opção de mensagens desmarcada. A orientação a responsáveis usa o canal da recepção quando a clínica configurou contato. Publicações antigas conservam a seleção anterior até a próxima publicação pelo proprietário.

A entrega de WhatsApp em um aparelho real, o cadastro online por responsável e a integração direta das reservas públicas com a identidade clínica ainda precisam de validação ou implementação. As áreas clínicas e financeiras que esta etapa reutiliza conservam as restrições já existentes. Os testes por módulo e as linhas em aberto estão abaixo.

## Referências

- [Menus e fluxos do Simples Dental](https://ajuda.simplesdental.com/pt-BR/collections/3123956-menus)
- [Primeiros passos do menu Ajustes](https://ajuda.simplesdental.com/pt-BR/articles/12541214-primeiros-passos-do-menu-ajustes)
- [Configuração do link de agendamento](https://ajuda.simplesdental.com/pt-BR/articles/10548422-como-criar-o-link-de-agendamento)
- [Fila de encaixes](https://ajuda.simplesdental.com/pt-BR/articles/8912897-como-fazer-a-gestao-de-encaixe)
- [Recursos administrativos do OpenEMR](https://github.com/openemr/openemr/blob/master/Documentation/api/STANDARD_API.md)

## Matriz

| ID | Área | Funcionalidade | Origem e referência | Comportamento esperado | Orion antes | Implementação | Dependências | Testes | Validação visual | Deploy | Produção | Estado |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P01 | Profissionais | Cadastro interno sem convite | Ajustes › Equipe | Criar profissional disponível para configurar agenda | Parcial | Cadastro e expediente integrados à Agenda | Profissionais e Agenda | Interface com banco | Celular e computador | Publicado | Formulário na conta | Validado |
| P02 | Profissionais | CRO e UF cadastrais | Orion | Registrar o conselho sem conceder acesso clínico | Ausente | Cadastro e expediente integrados à Agenda | Profissionais | Interface com banco | Celular e computador | Publicado | Formulário na conta | Validado |
| P03 | Profissionais | Múltiplas especialidades | Orion | Selecionar e preservar especialidades odontológicas | Parcial | Cadastro e expediente integrados à Agenda | Profissionais | Interface com banco | Celular e computador | Publicado | Formulário na conta | Validado |
| P04 | Profissionais | Editar profissional | Ajustes › Equipe | Editar dados com proteção contra atualização concorrente | Parcial | Atualização e consultório preservados | Profissionais | PostgreSQL e API | — | Publicado | — | Validado em teste |
| P05 | Profissionais | Inativar e reativar | Ajustes › Equipe | Preservar registros e retirar inativos de novos horários | Implementado no motor | — | Profissionais | — | — | — | — | P0 |
| P06 | Profissionais | Expediente e intervalos | Ajustes › Equipe › Horários | Configurar disponibilidade por dia e intervalos de atendimento | Parcial | Cadastro e expediente integrados à Agenda | Horários do profissional | Interface com banco | Celular e computador | Publicado | Formulário na conta | Validado |
| P07 | Profissionais | Procedimentos habilitados | Ajustes › Equipe | Exibir vínculo aplicável ao agendamento interno | Parcial | Cadastro e expediente integrados à Agenda | Profissionais e Procedimentos | Interface com banco | Celular e computador | Publicado | Formulário na conta | Validado |
| P08 | Profissionais | Consultório padrão | Orion | Sugerir cadeira cadastrada e ativa | Ausente | Atualização e consultório preservados | Profissionais e Consultórios | PostgreSQL e API | — | Publicado | — | Validado em teste |
| P09 | Profissionais | Convites e permissões | Ajustes › Equipe › Convidar | Separar registro, acesso ao Orion e autorização clínica | Parcial | — | Equipe e permissões | — | — | — | — | P1 |
| A01 | Agenda | Visualização diária | Agenda › Dia | Navegar e abrir consultas por data | Implementado | Grade existente preservada | Agenda Orion | Navegação e regressão | 10 combinações | Publicado | Agenda na conta | Validado em teste |
| A02 | Agenda | Visualização semanal | Agenda › Semana | Navegar a semana | Implementado | Grade existente preservada | Agenda Orion | Navegação e regressão | 10 combinações | Publicado | Agenda na conta | Validado em teste |
| A03 | Agenda | Visão mensal | Agenda › calendário | Navegar mês e selecionar data | Implementado | Grade existente preservada | Agenda Orion | Navegação e regressão | 10 combinações | Publicado | Agenda na conta | Validado em teste |
| A04 | Agenda | Filtrar profissional | Agenda › profissional | Restringir atendimento por profissional | Implementado | Grade existente preservada | Agenda Orion | Navegação e regressão | 10 combinações | Publicado | Agenda na conta | Validado em teste |
| A05 | Agenda | Selecionar paciente | Agenda › Nova consulta | Buscar por identidade própria com telefone compartilhado | Ausente no agendamento | Formulário e reserva integrada | Agenda e cadastro | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A06 | Agenda | Cadastro rápido | Agenda › Nova consulta | Cadastrar com informações administrativas mínimas | Ausente no agendamento | Formulário e reserva integrada | Agenda e cadastro | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A07 | Agenda | Escolher procedimento | Agenda › Nova consulta | Oferecer procedimentos ativos habilitados | Parcial | Formulário e reserva integrada | Agenda e catálogo | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A08 | Agenda | Ajustar duração | Agenda › Nova consulta | Ajustar duração da consulta, inclusive no conflito | Ausente no fluxo | Formulário e reserva integrada | Motor e Agenda | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A09 | Agenda | Horário livre | Agenda › Encontrar horário livre | Exibir disponibilidade e validar no salvamento | Parcial | Formulário e reserva integrada | Motor e Agenda | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A10 | Agenda | Consultório ou cadeira | Ajuda › Agendar consulta | Reservar recurso e horário de forma atômica | Parcial, vínculo posterior | Formulário e reserva integrada | Motor e Consultórios | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A11 | Agenda | Observação administrativa | Agenda › Nova consulta | Registrar instruções da recepção em detalhe privado | Ausente no agendamento | Formulário e reserva integrada | Agenda e detalhe | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A12 | Agenda | Tipo de consulta | Orion | Distinguir avaliação, retorno, sessão e urgência | Ausente no fluxo | Formulário e reserva integrada | Agenda e detalhe | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A13 | Agenda | Confirmação do agendamento | Agenda › Nova consulta | Confirmar a reserva e exibi-la na agenda | Parcial | Formulário e reserva integrada | Motor e Agenda | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A14 | Agenda | Remarcação | Agenda › Consulta | Preservar duração, paciente e consultório | Parcial | Conserva paciente, cadeira e duração | Motor e Agenda | PostgreSQL e API | — | Publicado | — | Validado em teste |
| A15 | Agenda | Cancelamento | Agenda › Consulta | Guardar histórico e liberar recursos | Implementado no motor | Formulário e reserva integrada | Motor e Agenda | Interface e PostgreSQL | Celular e computador | Publicado | Formulário na conta | Validado em teste |
| A16 | Agenda | Registrar falta | Agenda › Consulta | Atualizar estado sem perder histórico | Implementado no motor | Fluxo nativo preservado | Motor e Agenda | Operações em PostgreSQL | — | Publicado | — | Validação odontológica completa pendente |
| A17 | Agenda | Concluir atendimento | Agenda › Consulta | Concluir e preservar histórico financeiro | Implementado no motor | Fluxo nativo preservado | Motor e Financeiro | Operações em PostgreSQL | — | Publicado | — | Validação odontológica completa pendente |
| A18 | Agenda | Compromisso ou bloqueio | Agenda › Compromisso | Bloquear profissional em período definido | Parcial | — | Agenda e bloqueios | — | — | — | — | P1 |
| A19 | Agenda | Compromisso de dia inteiro | Agenda › Compromisso | Bloquear o dia inteiro | A verificar | — | Agenda e bloqueios | — | — | — | — | P1 |
| A20 | Agenda | Recorrência de compromissos | Agenda › Compromisso | Configurar repetição sem colisões | A verificar | — | Agenda e bloqueios | — | — | — | — | P1 |
| A21 | Agenda | Retorno programado | Agenda › Retornar em | Criar tarefa ou retorno associado à consulta | A verificar | — | Agenda e ficha | — | — | — | — | P1 |
| A22 | Agenda | Fila de encaixes | Agenda › Encaixes | Buscar horário para pacientes na fila | Ausente no fluxo revisado | — | Agenda e recepção | — | — | — | — | P2 |
| A23 | Agenda | Confirmação e lembrete | Agenda › Nova consulta | Respeitar opt-in e canal disponível | Parcial | — | WhatsApp e Agenda | — | — | — | — | P2 |
| A24 | Agenda | Rótulo de consulta | Agenda › Nova consulta | Classificar visualmente consultas | Ausente no fluxo | — | Agenda | — | — | — | — | P2 |
| A25 | Agenda | Visualização compacta | Agenda › Preferências | Ajustar densidade da grade | A verificar | — | Agenda | — | — | — | — | P2 |
| A26 | Agenda | Ocultar sábado e domingo | Agenda › Preferências | Ajustar os dias exibidos | A verificar | — | Agenda | — | — | — | — | P2 |
| A27 | Agenda | Mostrar cancelados | Agenda › Preferências | Filtrar cancelamentos no calendário | Parcial | — | Agenda | — | — | — | — | P2 |
| C01 | Pacientes | Listar e pesquisar | Pacientes | Buscar por nome, telefone e CPF com escopo da clínica | Parcial | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C02 | Pacientes | Cadastro básico | Pacientes › Novo paciente | Registrar identificação, nascimento e contatos | Parcial | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C03 | Pacientes | Responsável legal | Pacientes › Novo paciente | Registrar responsável independente do paciente | Parcial | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C04 | Pacientes | Responsável financeiro | Orion | Registrar o pagador sem atribuir o prontuário a ele | Ausente | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C05 | Pacientes | Dados complementares | Pacientes › Informações adicionais | Registrar identificação, e-mail e dados administrativos úteis | Ausente | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C06 | Pacientes | Endereço | Pacientes › Endereço | Cadastrar endereço opcional | Ausente | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C07 | Pacientes | Convênio e carteirinha | Pacientes › Plano | Vincular plano e identificação do beneficiário | Ausente no cadastro | Ficha, edição e consultas da recepção | Pacientes e Convênios | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C08 | Pacientes | Origem e etiquetas | Pacientes › Novo paciente | Registrar origem e classificar paciente | Ausente | — | Pacientes | — | — | — | — | P2 |
| C09 | Pacientes | Editar e auditar | Pacientes › Ficha › Editar | Atualizar com versão e trilha de autoria | Parcial | Ficha, edição e consultas da recepção | Pacientes | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C10 | Pacientes | Resumo e histórico de consultas | Pacientes › Sobre | Ver consultas do paciente sem misturar familiares | Parcial | Ficha, edição e consultas da recepção | Ficha e Agenda | Interface e PostgreSQL | 8 combinações | Publicado | Formulário na conta | Validado em teste |
| C11 | Pacientes | Orçamentos da ficha | Pacientes › Orçamentos | Acessar os orçamentos do paciente autorizado | Parcial | Entrada na seção clínica da ficha | Ficha e Orçamentos | Navegação, autorização e registros | Combinações de navegador e tema | Publicado | — | Validação clínica completa pendente |
| C12 | Pacientes | Tratamentos da ficha | Pacientes › Tratamentos | Acessar planos no contexto do paciente | Parcial | Entrada na seção clínica da ficha | Ficha e Planos | Navegação, autorização e registros | Combinações de navegador e tema | Publicado | — | Validação clínica completa pendente |
| C13 | Pacientes | Evolução do paciente | Pacientes › Tratamentos | Registrar evolução com autoria e histórico | Parcial | Entrada na seção clínica da ficha | Ficha e Prontuário | Navegação, autorização e registros | Combinações de navegador e tema | Publicado | — | Validação clínica completa pendente |
| C14 | Pacientes | Ficha de anamnese | Pacientes › Anamnese | Selecionar versão do formulário e salvar respostas | Implementado no núcleo | Entrada na seção clínica da ficha | Ficha e Prontuário | Navegação, autorização e registros | Combinações de navegador e tema | Publicado | — | Validação clínica completa pendente |
| C15 | Pacientes | Exames e imagens | Pacientes › Imagens | Acessar anexos protegidos do paciente | Parcial | Entrada na seção clínica da ficha | Ficha e Exames | Navegação, autorização e registros | Combinações de navegador e tema | Publicado | — | Validação clínica completa pendente |
| C16 | Pacientes | Documentos | Pacientes › Documentos | Acessar documentos e modelos clínicos autorizados | Parcial | — | Prontuário e Documentos | — | — | — | — | P1 |
| C17 | Pacientes | Débitos da ficha | Pacientes › Débitos | Apresentar lançamentos da pessoa sem misturar telefone compartilhado | Ausente no contexto | — | Financeiro e Ficha | — | — | — | — | P2 |
| C18 | Pacientes | Exportação do cadastro | Pacientes › Exportar | Exportar os dados autorizados com auditoria | A verificar | — | Pacientes | — | — | — | — | P2 |
| S01 | Atendimento | Link público opcional | Agenda › Link de agendamento | Configurar somente quando a clínica optar por usar | Parcial | Link permanece opcional, com publicação independente | Meu site | Consulta interna com site fora do ar | Prévia da Ana Clara | Publicado | Prévia acessível | Validado |
| S02 | Atendimento | Primeira consulta ou retorno | Ajuda › Link de agendamento | Apresentar entrada simples para pacientes | Ausente no fluxo | Seleção explícita de avaliação ou retorno | Site público | Publicação, reserva, retirada e bloqueio de outras escolhas | Celular emulado | Candidato | — | Validado em teste |
| S03 | Atendimento | Escolher horários públicos | Ajuda › Link de agendamento | Mostrar apenas procedimentos elegíveis configurados | Parcial | Seleção explícita de avaliação ou retorno | Site público e catálogo | Publicação, reserva, retirada e bloqueio de outras escolhas | Celular emulado | Candidato | — | Validado em teste |
| S04 | Atendimento | Identificação de responsável online | Ajuda › Link de agendamento | Coletar dados mínimos e separar beneficiário de contato | Ausente no fluxo | Encaminha à recepção quando há contato | Site público | Exibição do atalho | Celular emulado | Candidato | — | Cadastro por responsável pendente |
| G01 | Gestão | Consultórios e cadeiras | Ajustes › Cadeiras | Cadastrar e preservar conflito de recursos | Implementado no núcleo | — | Consultórios e Agenda | — | — | — | — | P0 |
| G02 | Gestão | Modelos de anamnese | Ajustes › Anamnese | Editar modelos com versionamento | Implementado no núcleo | — | Ajustes e Clínica | — | — | — | — | P1 |
| G03 | Gestão | Modelos de contrato | Ajustes › Contrato | Gerar documentos aprovados com controles | A verificar | — | Documentos | — | — | — | — | P2 |
| G04 | Gestão | Plano e convênios | Ajustes › Planos | Cadastrar catálogo privado de planos | Implementado no núcleo | — | Convênios | — | — | — | — | P1 |
| G05 | Gestão | Categorias administrativas | Ajustes › Categorias | Classificar entradas e saídas | Parcial | — | Financeiro | — | — | — | — | P2 |
| G06 | Gestão | Contas financeiras | Ajustes › Contas financeiras | Organizar recebimentos e conciliação | A verificar | — | Financeiro | — | — | — | — | P2 |
| G07 | Gestão | Fluxo de caixa | Financeiro | Apresentar lançamentos, filtros e recebimentos | Implementado no núcleo | — | Financeiro | — | — | — | — | P2 |
| G08 | Gestão | Oportunidades | Vendas | Acompanhar orçamento e paciente com acesso permitido | Parcial | — | Vendas e Orçamentos | — | — | — | — | P2 |
| G09 | Gestão | Serviços de prótese | Controle de prótese | Registrar pedidos, prazos e laboratório | Implementado no núcleo | — | Prótese e laboratório | — | — | — | — | P2 |
| G10 | Gestão | Materiais e histórico | Estoque | Consultar movimentações e níveis | Implementado no núcleo | — | Estoque | — | — | — | — | P2 |
| G11 | Gestão | Indicações | Marketing | Acompanhar origem e indicações de pacientes | A verificar | — | Pacientes e Atendimento | — | — | — | — | P2 |
| G12 | Gestão | Indicadores clínicos | Inteligência › Indicadores | Exibir volume, faltas e ocupação com dados reais | Parcial | — | Dashboard e Relatórios | — | — | — | — | P2 |
| G13 | Gestão | Relatórios | Inteligência › Relatórios | Filtrar dados administrativos e clínicos por permissão | Parcial | — | Relatórios | — | — | — | — | P2 |
| G14 | Gestão | Tarefas | Inteligência › Tarefas | Organizar pendências de recepção | A verificar | — | Dashboard e Equipe | — | — | — | — | P2 |
| G15 | Gestão | Ortodontia e manutenções | Inteligência › Ortodontia | Acompanhar pacientes, retornos e tratamentos | Parcial | — | Planos de tratamento | — | — | — | — | P3 |
| G16 | Atendimento | Recepção IA | Inteligência › Secretária IA | Usar dados administrativos e encaminhar ao humano | Parcial | — | Conversas e WhatsApp | — | — | — | — | P2 |
| G17 | Atendimento | Modelos de mensagem | Ajustes › Copiloto | Usar modelos autorizados sem copiar integrações | A verificar | — | WhatsApp e Automações | — | — | — | — | P2 |
| G18 | Atendimento | Configuração de comunicações | Ajustes › Comunicação | Definir lembretes e consentimentos | Parcial | — | WhatsApp e Automações | — | — | — | — | P2 |
| G19 | Gestão | Nota fiscal | Ajustes › Nota fiscal | Depende de integração fiscal e validação | A verificar | — | Financeiro | — | — | — | — | P3 |
| G20 | Gestão | Taxas e conciliação de cartão | Ajustes › Taxas Maquininha | Configurar taxas no financeiro próprio | A verificar | — | Financeiro | — | — | — | — | P3 |
| G21 | Atendimento | Meu Doutor | Ajustes › Meu Doutor | Área dependente de disponibilidade da referência | Não investigado | — | Integrações | — | — | — | — | P3 |
| G22 | Gestão | Simples Pay | Simples Pay | Serviço proprietário excluído pelo pedido | Fora do escopo | — | Sem integração | — | — | — | — | Excluído |
| G23 | Gestão | Loja | Loja | Serviço proprietário, depende de habilitação e avaliação | A verificar | — | Integrações | — | — | — | — | P3 |
| K01 | Clínica | Odontograma permanente e decíduo | Orion e referências abertas | Medidas, faces, autoria e histórico | Implementado no núcleo | — | Prontuário | — | — | — | — | P1 |
| K02 | Clínica | Periodontograma | Referências abertas | Medições e comparação entre avaliações | Parcial | — | Prontuário | — | — | — | — | P3 |
| K03 | Clínica | DICOM e CBCT | Referências abertas | Visualização após validação profissional e técnica | Ausente | — | Exames e imagens | — | — | — | — | P3 |
| K04 | Clínica | Prescrição e assinatura | Paciente › Documentos | Depende de profissional, provedor e validação | A verificar | — | Prontuário | — | — | — | — | P3 |

## Medição inicial

Produção, conta da Ana Clara, navegador a 397 px. Tempo entre interação e formulário visível, em uma amostra. O formulário de profissional levou 324 ms. A abertura do formulário da Agenda levou 1.292 ms. A estrutura do diretório levou 892 ms. Esses tempos incluem a interação do navegador. São amostras de diagnóstico, sem estimativa de percentil.

A lista de Equipe aguardava catálogo, vínculos e permissões antes de renderizar a ação principal. O formulário de consulta carregava até 200 contatos junto do catálogo e exigia telefone. O cadastro odontológico existente exigia autorização clínica para os dados administrativos. A agenda e os cadastros compartilhados continuam como fontes de dados.

## Repositórios de referência

O GitHub confirmou os 32 repositórios listados no briefing. A tabela registra a licença reconhecida pelo catálogo, a última atividade indicada e uma amostra da árvore quando a consulta retornou código. Os caminhos são pontos de leitura para investigação. A implementação desta etapa usa código da Orion.

| Repositório | Licença indicada | Atividade | Arquivo identificado |
| --- | --- | --- | --- |
| [abinauv/dental-erp](https://github.com/abinauv/dental-erp) | MIT | 2026-11-23 | [page.tsx](https://github.com/abinauv/dental-erp/blob/main/app/%28dashboard%29/appointments/%5Bid%5D/edit/page.tsx) |
| [bardurt/odontograma](https://github.com/bardurt/odontograma) | MIT | 2024-09-21 | [odontogramaGenerator.js](https://github.com/bardurt/odontograma/blob/master/js/odontogramaGenerator.js) |
| [biomathcode/react-odontogram](https://github.com/biomathcode/react-odontogram) | Revisão da licença pendente | 2026-09-08 | [index.tsx](https://github.com/biomathcode/react-odontogram/blob/main/packages/react-native-odontogram/src/index.tsx) |
| [clawnify/OpenDentist](https://github.com/clawnify/OpenDentist) | MIT | 2026-11-17 | [agenda-page.tsx](https://github.com/clawnify/OpenDentist/blob/master/src/client/components/agenda/agenda-page.tsx) |
| [danielbatres/orthodontic-dentistry-clinical-management](https://github.com/danielbatres/orthodontic-dentistry-clinical-management) | MIT | 2023-10-26 | [orthodontic-dentistry-db.sql](https://github.com/danielbatres/orthodontic-dentistry-clinical-management/blob/main/database/orthodontic-dentistry-db.sql) |
| [davidegironi/dentned](https://github.com/davidegironi/dentned) | Revisão da licença pendente | 2026-11-06 | [FormAppointments.Designer.cs](https://github.com/davidegironi/dentned/blob/master/DentneD/Forms/FormAppointments.Designer.cs) |
| [dentalpin/dentalpin](https://github.com/dentalpin/dentalpin) | Revisão da licença pendente | 2026-11-24 | [0002_clinic_timezone.py](https://github.com/dentalpin/dentalpin/blob/main/backend/alembic/versions/0002_clinic_timezone.py) |
| [denysdovhan/voxel-viewer](https://github.com/denysdovhan/voxel-viewer) | Revisão da licença pendente | 2026-11-17 | [useViewerApp.ts](https://github.com/denysdovhan/voxel-viewer/blob/main/src/app/useViewerApp.ts) |
| [elselawi/apexo](https://github.com/elselawi/apexo) | GPL-3.0 | 2026-11-14 | [appointments.dart](https://github.com/elselawi/apexo/blob/main/integration_test/groups/appointments.dart) |
| [igorsfreitas/periodontograma](https://github.com/igorsfreitas/periodontograma) | Revisão da licença pendente | 2016-07-11 | [counter.html](https://github.com/igorsfreitas/periodontograma/blob/master/Periodontograma_files/counter.html) |
| [incared-proyectos/odontograma](https://github.com/incared-proyectos/odontograma) | Revisão da licença pendente | 2025-08-18 | [Odontograma.js](https://github.com/incared-proyectos/odontograma/blob/main/Scripts/Odontograma.js) |
| [medplum/medplum](https://github.com/medplum/medplum) | Apache-2.0 | 2026-11-24 | Consulta da árvore pendente |
| [Mostafa-Badawey/Dentigo-Dental-Case-Management](https://github.com/Mostafa-Badawey/Dentigo-Dental-Case-Management) | Revisão da licença pendente | 2026-11-14 | Consulta da árvore pendente |
| [neo1512-morpheus/dicom-viewer](https://github.com/neo1512-morpheus/dicom-viewer) | MIT | 2026-06-05 | [index.html](https://github.com/neo1512-morpheus/dicom-viewer/blob/main/Viewers/.netlify/www/index.html) |
| [OHIF/Viewers](https://github.com/OHIF/Viewers) | MIT | 2026-11-24 | [dicom-json-generator.js](https://github.com/OHIF/Viewers/blob/master/.scripts/dicom-json-generator.js) |
| [openemr/openemr](https://github.com/openemr/openemr) | GPL-3.0 | 2026-11-24 | [clinical_notes_documents.html](https://github.com/openemr/openemr/blob/master/Documentation/EHI_Export/docs/tables/clinical_notes_documents.html) |
| [openmrs/openmrs-core](https://github.com/openmrs/openmrs-core) | Revisão da licença pendente | 2026-11-23 | Consulta da árvore pendente |
| [orthanc-mirrors/orthanc](https://github.com/orthanc-mirrors/orthanc) | GPL-3.0 | 2026-11-21 | [DicomDataSource.cpp](https://github.com/orthanc-mirrors/orthanc/blob/master/OrthancFramework/Sources/DataSource/DicomDataSource.cpp) |
| [pedroso-dev/dental-ia-web](https://github.com/pedroso-dev/dental-ia-web) | Revisão da licença pendente | 2026-07-29 | Consulta da árvore pendente |
| [Rathetsu/Dental-Lab-Management-System](https://github.com/Rathetsu/Dental-Lab-Management-System) | MIT | 2023-09-05 | [NewDentistForm.js](https://github.com/Rathetsu/Dental-Lab-Management-System/blob/master/DentalLabManagementSystem/src/screens/NewDentistForm.js) |
| [rowinggolfer/openmolar1](https://github.com/rowinggolfer/openmolar1) | Revisão da licença pendente | 2024-02-16 | [test_add_clinician_dialog.py](https://github.com/rowinggolfer/openmolar1/blob/master/src/omtests/qt4gui/dialogs/test_add_clinician_dialog.py) |
| [rowinggolfer/openmolar2](https://github.com/rowinggolfer/openmolar2) | GPL-3.0 | 2022-10-18 | [admin_appointments.py](https://github.com/rowinggolfer/openmolar2/blob/master/src/lib_openmolar/admin/db_orm/admin_appointments.py) |
| [sanjibani/open-dental-mcp](https://github.com/sanjibani/open-dental-mcp) | MIT | 2026-06-26 | Árvore sem caminho específico nesta consulta |
| [Slicer/Slicer](https://github.com/Slicer/Slicer) | Revisão da licença pendente | 2026-11-24 | [DICOMPluginTests.py](https://github.com/Slicer/Slicer/blob/main/Applications/SlicerApp/Testing/Python/DICOMPluginTests.py) |
| [souhaibouadi/dental](https://github.com/souhaibouadi/dental) | Revisão da licença pendente | 2026-05-13 | [__init__.py](https://github.com/souhaibouadi/dental/blob/main/dental_clinic/__init__.py) |
| [ViolettaNcl/DentalClinic](https://github.com/ViolettaNcl/DentalClinic) | MIT | 2026-11-13 | [Appointmentreminderservice.cs](https://github.com/ViolettaNcl/DentalClinic/blob/main/BackgroundJobs/Appointmentreminderservice.cs) |
| [wpoch/odontogram](https://github.com/wpoch/odontogram) | GPL-2.0 | 2026-02-09 · arquivado | [estado_odontograma.js](https://github.com/wpoch/odontogram/blob/master/estado_odontograma.js) |
| [yagizyagli/universal_dental_ai](https://github.com/yagizyagli/universal_dental_ai) | Apache-2.0 | 2026-08-24 | [dicom_reader.py](https://github.com/yagizyagli/universal_dental_ai/blob/main/src/universal_dental_ai/core/dicom_reader.py) |
| [ZoliQua/Angular-Advanced-Odontogram](https://github.com/ZoliQua/Angular-Advanced-Odontogram) | MIT | 2026-11-23 | [app-version.spec.ts](https://github.com/ZoliQua/Angular-Advanced-Odontogram/blob/main/projects/angular-advanced-odontogram/src/lib/app-version.spec.ts) |
| [ZoliQua/Dental-CAD-Designer](https://github.com/ZoliQua/Dental-CAD-Designer) | Apache-2.0 | 2026-11-20 | [diagnosticBundle.no-egress.test.ts](https://github.com/ZoliQua/Dental-CAD-Designer/blob/main/apps/client/src/engine/diagnosticBundle.no-egress.test.ts) |
| [ZoliQua/Dental-CBCT-Viewer](https://github.com/ZoliQua/Dental-CBCT-Viewer) | Revisão da licença pendente | 2026-11-23 | [DisclaimerBanner.tsx](https://github.com/ZoliQua/Dental-CBCT-Viewer/blob/main/src/components/dicom/DisclaimerBanner.tsx) |
| [ZoliQua/React-Advanced-Odontogram](https://github.com/ZoliQua/React-Advanced-Odontogram) | MIT | 2026-11-23 | [App.CaseDiagnosesModal.html](https://github.com/ZoliQua/React-Advanced-Odontogram/blob/main/docs/functions/App.CaseDiagnosesModal.html) |
