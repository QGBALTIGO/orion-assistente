# Reconstrução da Orion Odontologia

A matriz distingue o comportamento pedido, a referência funcional e o resultado da implementação. A coluna “Estado atual da Orion” descreve a base examinada em `29573ae`. “Implementado” nessa coluna indica uma capacidade identificada no código existente. A liberação da nova experiência depende das colunas de testes, produção e estado. Filtre a matriz por área, estado ou ID. Uma célula de deploy vazia significa que a reconstrução daquela linha ainda não tem publicação registrada.

## Evidência de origem

Em 22/11/2026 a conta autorizada do Simples Dental permitiu percorrer Inteligência, Pacientes, Agenda, Vendas, Financeiro, Controle de prótese, Marketing, Estoque, Loja e Ajustes. A inspeção incluiu a ficha de um paciente sintético cadastrado apenas com nome. Não foram usadas informações identificáveis de pacientes reais nos testes. Nas seções pagas ou desabilitadas, o registro se limita ao controle observado. O inventário de menus contextuais, operações financeiras, fluxos de documentos, permissões adicionais e estados responsivos da referência permanece aberto. O fluxo de compra de produtos da referência, a ativação de automações comerciais e o Simples Pay não foram executados.

A lista de repositórios registra revisão, arquivo de licença e arquivos examinados. É uma inspeção amostral de implementação, não a execução dos projetos nem parecer jurídico. O projeto Dentigo forneceu documentação de casos. O código de cada outro projeto foi inspecionado em profundidade variável. Não foi incorporado código, fonte, imagem, modelo ou SDK desses projetos. A interface usa Figtree, Outfit, cores semânticas, foco, superfícies e botões existentes da Orion. Alterações de CSS específicas ficam restritas ao espaço odontológico.

## Ordem e dependências

1. Operação administrativa na recepção a partir da agenda e do fuso da empresa. A entrada em produção usa a imagem do serviço que executa o painel e a API.
2. Separação entre paciente, contato que agenda, responsável e pagador. O vínculo com uma consulta exige identidade explícita. Um número compartilhado não comprova a identidade de um paciente.
3. Perfis clínicos e auditoria antes de introduzir prontuário, odontograma, periodontograma ou anexos para uso real.
4. Vínculo por ID entre plano, orçamento, execução, recebíveis e laboratório, sempre com registro de autoria e versão.
5. Integrações, especialidades e documentos podem exigir homologação profissional ou contratos externos. A publicação deve conservar os registros históricos.

## Validação clínica e operacional

Para liberar prontuários, confirmar com cirurgião-dentista habilitado a terminologia, as identificações profissionais, o conteúdo do registro, a distinção entre rascunho e registro final, as retificações e o uso da numeração e das faces. O periodontograma exige validação das escalas e das convenções de sinal, além de comparação com casos sintéticos de referência. A prescrição exige validação específica da assinatura, identificação e conteúdo pela equipe responsável. Registrar responsável, versão testada, data e escopo do aceite. Imagens e CBCT destinados a uso diagnóstico dependem de avaliação do uso pretendido e das exigências aplicáveis.

A equipe responsável também precisa documentar bases e finalidades do tratamento, acesso por função, revogação, guarda, exportação, auditoria e um ensaio de restauração do banco junto às chaves e aos anexos. A existência de criptografia e testes técnicos não certifica atendimento integral às normas.

Fontes oficiais para a revisão profissional: [Manual do Prontuário do Paciente em Odontologia, CFO](https://website.cfo.org.br/wp-content/uploads/2026/03/CFO_Manual_do_Prontuario_Ebook_v2.pdf), [LGPD](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm), [Lei 13.787/2018](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13787.htm) e [Anvisa, dispositivos médicos](https://www.gov.br/anvisa/pt-br/centraisdeconteudo/publicacoes/produtos-para-a-saude/manuais/software-como-dispositivo-medico-perguntas-e-respostas).

## Entrega administrativa da recepção

Os indicadores usam uma única leitura consistente dos estados confirmada, concluída, cancelada e falta. O servidor considera a data local da empresa, ignora reservas provisórias e mostra até cinco horários confirmados que ainda vão começar no dia. O resumo traz horário e profissional. O endpoint não consulta telefone, identidade de pacientes, anamnese, prontuários ou valores financeiros. O painel atualiza a cada 30 segundos enquanto visível. Uma falha mantém a última leitura identificada e agenda nova tentativa. A navegação para Agenda, Financeiro e criação respeita as autorizações existentes.

Validação local da entrega: 19 testes de integração com PostgreSQL 16, 102 testes unitários do núcleo e 202 testes das áreas compartilhadas. Passaram 127 testes de interface e oito verificações em navegador nos temas claro e escuro. O teste do dashboard compartilhado usava um controle antigo para mudar a semana; o teste agora aciona a navegação efetivamente apresentada e verifica a data. A inspeção visual encontrou quebra de palavras em tablet com o menu aberto e em tela de 320 px; a grade agora usa a largura disponível e os rótulos são verificados em navegador. Não houve alteração de esquema para esta entrega.

Em 22/11/2026, a imagem do commit `f32e15ac` foi publicada no serviço da API, implantação `b4ceba29-4cd5-42dd-b0da-664023604e92`. O painel autenticado da clínica exibiu os quatro indicadores. “Ver agenda” abriu a Agenda e “Início” retornou à Recepção. Os arquivos JavaScript e CSS servidos pelos dois domínios corresponderam aos arquivos testados. O estado de saúde e o site público da Barbearia Peixoto responderam com HTTP 200 em ambos. O teste HTTP adicional de onboarding usava uma conta que já possuía empresa e pedia comissão avançada na fase inicial inativa. O cenário agora testa a recusa dessa comissão, o cadastro de uma empresa por conta e os sete passos aceitos. Os 18 testes adicionais com PostgreSQL e Redis passaram, cobrindo também financeiro, site público e formatação do WhatsApp.

## Identidade do paciente na agenda

O contato que fez a reserva e o paciente atendido têm identidades distintas. Na ficha do paciente, “Consultas do paciente” lista somente vínculos explícitos. A equipe autorizada pode localizar uma consulta por data, conferir horário, profissional, protocolo e contato, e então vinculá-la. A troca de paciente e o desvínculo geram eventos com autor, horário e versão. O reagendamento no mesmo registro conserva a associação. Um novo registro de agenda requer seleção explícita. O telefone nunca preenche o vínculo automaticamente.

A migração 0087 cria uma tabela separada e índices. Não reescreve agendamentos nem exige atribuir um paciente retrospectivamente. No pré-deploy, o fluxo existente verifica o backup privado por leitura do objeto, verifica a revisão anterior e valida o contrato de segurança depois da migração. Para reverter a aplicação após existirem vínculos, preservar a tabela e usar a imagem anterior com verificação somente leitura do contrato compatível. A descida de esquema com registros é recusada. A ficha está disponível a usuários que já têm autorização administrativa odontológica ou clínica na empresa. Nenhum acesso a prontuários é concedido por essa operação.

A ficha usa confirmação no próprio painel antes de transferir ou desfazer um vínculo. Nos testes com dois pacientes sintéticos que compartilham o telefone do responsável, uma disputa simultânea pela mesma versão resultou em uma gravação e um conflito. A repetição da mesma solicitação conservou a versão. O teste de navegador cobriu a desistência da confirmação, o retorno do foco, a navegação por teclado e quatro larguras nos dois temas. A grade da ficha usa a largura que sobra após a abertura do menu lateral.

Na verificação adicional da referência, a ficha sintética apresentou cinco categorias de documentos, cada uma com criação e histórico próprios. O editor de receituário ofereceu as modalidades impressa e digital, esta última por um prestador externo. No editor impresso foram inspecionados profissional, data e seleção do medicamento. O editor foi fechado sem emitir um documento. Em Tratamentos, foram inspecionados plano, procedimento, alternância de dentição, seleção de região e acesso à evolução.

O ensaio de recuperação criou uma base PostgreSQL 16 isolada na revisão 0086, com uma clínica e paciente sintéticos. Um arquivo pg_dump em formato custom foi restaurado em um segundo banco. Foram comparados os identificadores de paciente e empresa, o nome e a autoria. A cópia restaurada manteve a revisão 0086 e o RLS. A origem avançou para 0087 com zero vínculos presumidos. O ensaio não utilizou uma cópia de produção.

A revisão `3f71cfe5` passou no CI clínico 45076681302. O Railway concluiu a implantação `a531922f-c855-4e69-8fc5-b0c54d0ac7c7`. O serviço registrou o esquema clínico pronto. Foram conferidos os arquivos de JavaScript e CSS por conteúdo em ambos os domínios, a saúde do serviço, a resposta 401 sem sessão e a disponibilidade do site público da Barbearia Peixoto. O painel autenticado exibiu a restrição de acesso ao prontuário na conta sem concessão. O fluxo de escrita foi executado apenas com pacientes sintéticos em banco de teste.

No CI geral, a base anterior 29573ae e 3f71cfe5 apresentaram as mesmas 19 falhas de interface do simulador e a mesma falta da dependência Playwright na coleta de três testes Python. Os testes clínicos, o ensaio de restauração e os testes específicos de regressão foram executados separadamente. O CI geral permanece pendente de correção.

## Catálogo de materiais

O menu Estoque abre um espaço de materiais dedicado. A busca, a ordenação alfabética e a paginação são feitas no banco para a clínica autorizada. A lista mostra os saldos reais e abre o histórico, a entrada e a saída do material selecionado. Se uma operação foi gravada e a resposta se perdeu, repetir o envio a partir do formulário conserva a chave original. O acesso usa as autorizações já existentes. A página dedicada não requisita a lista de pacientes nem seus registros clínicos.

A referência financeira também foi verificada em Fluxo de Caixa → Filtrar. Foram identificadas combinações de movimentação, situação de pagamento, nota fiscal, convênio, recorrência, recibo, meio de pagamento, conta, categoria e filtros salvos. As operações estão registradas individualmente na matriz.


## Caixa da clínica na recepção

O caixa resume os recebimentos, as despesas pagas, o saldo e as receitas em aberto do período selecionado. “Hoje” e “Mês até hoje” obedecem ao fuso da empresa. A consulta usa a permissão financeira e o mesmo livro de lançamentos do Financeiro. Uma consulta finalizada só entra no recebido quando há um lançamento pago. Para pendências, o período seleciona a data do lançamento. O resumo não substitui o extrato individual ou o controle de parcelas por paciente.

Na base PostgreSQL 16 sintética, um pagamento às 23h25 locais foi atribuído ao dia correto, mesmo com data UTC já no dia seguinte. A consulta concluída sem recebimento e as movimentações de outra empresa não entraram no caixa. O navegador verificou teclado, quatro larguras e os dois temas. Foram simuladas indisponibilidade do financeiro e chegada tardia da consulta mensal após voltar à opção “Hoje”.


O caixa passou na validação do PR 361, revisão `25e1ad8e`. O estoque passou na validação do PR 360 e na construção da imagem pelo workflow 45078476485. No registro desta etapa, o Railway mantinha preparada a troca para a imagem do estoque no serviço da API. O controle de aprovação recusou a aplicação imediata. A imagem da ficha de pacientes continuava ativa.

## Cadastro de pacientes

Nas clínicas com o módulo habilitado, a navegação apresenta “Pacientes” para as pessoas atendidas e “Contatos da agenda” para os cadastros administrativos históricos. O atalho da Recepção abre o mesmo cadastro odontológico protegido. Quem ainda precisa de uma concessão vê a orientação adequada. Um proprietário pode abrir as permissões existentes. O diretório guarda o nome, a data de nascimento, o telefone de contato e os dados do responsável. A consulta do prontuário requer uma ação explícita de um profissional autorizado.

A busca pelo nome ou número do paciente ou responsável transmite o termo no corpo da requisição, devolve apenas os dados necessários à identificação e permite percorrer as páginas. A ficha administrativa e o quadro de consultas funcionam sem carregar exames ou registros clínicos. A chave de cadastro é específica da empresa e do autor. Repetir o envio após perder a resposta recupera a mesma pessoa e gera um único evento de criação. Cadastro independente pelo mesmo responsável continua possível. A alteração preserva as tabelas e os identificadores existentes.

Foram usados PostgreSQL 16 com papéis restritos, um navegador com duas pessoas sintéticas da mesma família e testes de autorização, concorrência, alteração de cadastro e navegação. O acesso administrativo não concede validação de documentos ou autorização clínica. A medição em produção permanece pendente.

## Controle de próteses e prazos

No painel autenticado do Simples Dental, o quadro apresentou cinco etapas. O filtro de atraso pode ser combinado com profissional, etiqueta ou laboratório. A criação solicita paciente, profissional e descrição do serviço. O formulário vazio manteve o salvamento desabilitado. A inspeção terminou sem gravar dados.

A Orion ganhou uma entrada própria para o controle de próteses. O filtro de prazo combina com a etapa e permanece selecionado após atualizar um trabalho. O banco usa a meia-noite local da clínica. Um trabalho com vencimento hoje recebe uma indicação própria. Concluídos, cancelados e sem data ficam fora dos atrasos. Os filtros por profissional, etiqueta e laboratório e a associação de uma consulta de prova ou entrega continuam registrados para implementação.

Uma verificação com 200 trabalhos encerrados confirmou que a página seguinte alcança o trabalho atrasado mais antigo. O teste também cobriu dois fusos em dias distintos, mudança de etapa, papéis com e sem acesso clínico e isolamento entre clínicas. No navegador, o módulo abriu a ficha pela identidade do paciente após ação explícita e preservou os filtros durante a atualização de etapa. Foram verificadas quatro larguras de tela, em claro e escuro. Não há migração nesta entrega.

O cadastro de pacientes no PR 362 passou na validação do GitHub no workflow 45079834880. A versão do estoque permanece preparada para aprovação no Railway. O caixa, o cadastro e os prazos aguardam a sequência de publicação e verificação em produção.
