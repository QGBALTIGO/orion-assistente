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

## Acesso administrativo ao estoque

A publicação da revisão 73650175 foi confirmada no Railway em 22/11/2026, implantação 4bcd5bee-ac50-487f-9ca9-b871a1ba3b94. Na conta da proprietária da clínica Ana Clara, o menu Estoque abriu a rota correta e pediu uma concessão clínica indevida. A correção passa a verificar a propriedade ativa da empresa exclusivamente nos endpoints de materiais e movimentações. O catálogo dedicado carrega somente permissões e materiais. Os endpoints de exames e prontuários continuam verificando a concessão profissional.

Com dados sintéticos passaram 21 testes de integração com PostgreSQL 16, 102 testes unitários e 135 testes de interface. O navegador validou a busca, inclusão e movimentação de materiais em quatro larguras nos dois temas. O cenário da titular sem concessão agora é explícito no navegador e no banco. A verificação autenticada da correção em produção está pendente.

## Menu e ajustes da clínica

O painel com nicho Odontologia verificado no servidor organiza o trabalho em Recepção, Agenda, Pacientes, Prontuários, Controle de próteses, Estoque e Financeiro. Indicadores e relacionamento dependem do plano. A página Ajustes da clínica abre os cadastros operacionais e, para titulares habilitados, leva diretamente às cadeiras, aos modelos de anamnese e aos acessos clínicos. Os contatos antigos de agendamento ficam em Ajustes quando há um diretório odontológico dedicado. Os links históricos e as permissões de leitura são preservados. A tela só consulta dados do destino depois da seleção.

O teste de navegação abre o menu real do painel em 320, 390, 820 e 1440 px, nos temas claro e escuro. Aciona Ajustes e Cadeiras por teclado, confere a área tocável, o destino ativo, o carregamento de dados e os limites da tela. A inspeção das imagens identificou espaço excessivo sob o nome da clínica. A altura passou a acompanhar a identificação da empresa na odontologia. Foram aprovados 143 testes de interface, 23 de integração odontológica em PostgreSQL 16, um caso de caixa por fuso e 18 de regressão compartilhada. A publicação desta navegação e a conferência na conta autenticada são etapas distintas da validação sintética.

## Prova ou entrega vinculada à consulta do paciente

O profissional pode vincular uma peça recebida a uma consulta futura confirmada da ficha do mesmo paciente. Pode registrar prova ou entrega, substituir a consulta e desfazer o vínculo. A seleção guarda a versão da identidade registrada na agenda. Se o atendimento for posteriormente atribuído a outro paciente, a ficha anterior deixa de mostrar os dados do horário. Cada mudança cria uma versão do trabalho com autoria e evento de auditoria. Nenhum paciente é inferido a partir de um telefone compartilhado.

A API consulta as autorizações da clínica e, para um dentista com portal profissional, limita as opções à própria agenda. A correção foi exercitada com 23 testes de integração em PostgreSQL 16 e 142 testes de interface. O teste real de navegador percorreu filtro, recebimento, vínculo, finalidade, laboratório e abertura da ficha em oito combinações de largura e tema. O caso de resposta perdida preservou a mesma chave de operação em ambas as tentativas. A versão não introduz uma migração. A liberação e a conferência em produção estão pendentes.

## Caixa em produção e exportação do inventário da clínica

A recepção da conta Dentista Ana Clara exibiu o Caixa da clínica na publicação Railway `4f7df4b2-4f0b-457b-89dd-f99065054df1`, commit `25e1ad8e`. A seleção mudou de Hoje para Mês até hoje e voltou. Os arquivos da versão corresponderam byte a byte nos dois domínios. Saúde do serviço e site público da Barbearia Peixoto responderam com HTTP 200. A consulta odontológica anônima retornou 401.

A exportação de materiais usa os filtros e a ordenação aplicados na lista da clínica. O arquivo contém material, unidade e saldo. Um resultado vazio contém os cabeçalhos. Cada exportação bem-sucedida registra o autor sem dados de paciente. A verificação cobriu 25 testes em PostgreSQL, 102 unitários, 147 de interface e o download do arquivo real por teclado em oito combinações de largura e tema. A publicação da exportação permanece pendente.

No Simples Dental, a tela Vendas mostrou as etapas Em aberto, Em andamento, Ganha e Perdida. Uma chamada para encontrar oportunidades ofereceu analisar a base existente com IA. A conta permaneceu com essa ativação pendente. Os fluxos internos após a ativação ainda precisam de uma base de demonstração autorizada.

## Conferência dos ajustes da referência e regressões compartilhadas

O formulário de cadeiras abriu com um campo de nome e ações para salvar e fechar. Foi fechado sem gravar. Os ajustes da clínica exibiram o nome para comunicações, responsável, emissor de recibo, exigência de código para assinaturas eletrônicas e impressão com papel timbrado. O funil também apresentou criação de etapa e descreveu a atualização da oportunidade após aprovação do orçamento. Esses controles têm linhas próprias na matriz. As funções protegidas por ativação continuam pendentes de verificação.

A comparação entre a versão publicada do caixa e o menu odontológico encontrou as mesmas falhas em verificações antigas de regressão. Sete testes de migração ainda esperavam a revisão 0079 como a última. A verificação compartilhada agora exige uma única revisão final, 0087, e cada predecessor de 0077 até ela. Os testes do processo web e do worker conferem a barreira de horários simultâneos já presente e os respectivos limites de espera. A suíte passou nos 374 casos executados.

A conclusão concorrente de uma consulta gera exatamente um valor a receber. Uma falta não gera lançamento. Os testes de comunicações concluíam antes de a limpeza da base sintética encontrar uma referência financeira à consulta. As verificações agora conferem valor, situação pendente, vínculo da consulta, isolamento e quantidade de lançamentos. A remoção de dados ocorre somente na clínica sintética da fixture. Foram aprovados 68 testes de integração com PostgreSQL e Redis nas suítes de agendamento, ciclo de vida e mensagens, com o envio de mensagens simulado.

## Ocupação das cadeiras na recepção

A recepção calcula o tempo reservado dentro do expediente local cadastrado da clínica. Desconta o intervalo e considera cada cadeira ativa separadamente. Uma reserva que atravessa a meia-noite entra apenas na parte que cai dentro do expediente. Agendamentos cancelados, faltas e reservas temporárias vencidas ficam fora. A tela distingue expediente ausente, clínica fechada, ausência de cadeiras e ocupação real de zero.

O endpoint administrativo retorna somente dia, situação do expediente, número de cadeiras e durações agregadas. Não altera as permissões do prontuário. A leitura da agenda e do caixa continua se a consulta de ocupação falhar. A mudança usa as tabelas existentes, sem migração. No cenário sintético, 5 h 15 min reservadas em 16 h resultaram em 32,8%. Passaram 26 testes em PostgreSQL, 46 unitários clínicos, 149 testes de interface e oito verificações no Chromium em celular, tablet e desktop. A publicação e a conferência em produção estão pendentes.

## Compatibilidade da agenda recorrente e dos links de consulta

O diagnóstico da recorrência parava no marcador de 0079 e comunicava falha mesmo com as garantias de 0087 presentes. O teste de leitura agora reconhece as versões publicadas de recibos, encaixes, laboratórios e vínculos de paciente e confere as proteções exigidas em cada uma. A rotina antiga de migração continua recusando a reaplicação. Em PostgreSQL 16 com papéis sintéticos, o verificador de 0075 ficou pronto em uma base 0087. Passaram oito testes de recorrência e permissões, sete de links de consulta e 1.389 de contratos financeiros e inicialização com rede externa desabilitada.

A validação semanal agora mede os sete botões e as duas extremidades da grade visível. O Chromium percorreu dia, semana e mês em cinco tamanhos, em claro e escuro. A suíte complementar passou em 14 testes, incluindo 30 combinações geométricas. Foram mantidas as verificações de privilégios e os testes de navegador no CI. Falhas independentes identificadas nos testes de contraste das estrelas de avaliação e de interpretação de conflitos no WhatsApp seguem em investigação.

A auditoria de Vendas abriu os formulários de etapa e oportunidade e o painel de filtros. Foram conferidos o título obrigatório, os limites dos textos, o seletor opcional de paciente, o rótulo e os quatro filtros. Os formulários de referência foram fechados sem gravar registros.

## Estoque da proprietária em produção

Em 22/11/2026 à noite, a Railway concluiu a implantação ba71466c-c0c3-4c64-824d-2c270e4f444d da revisão 95d3bb76. A titular da clínica Dentista Ana Clara abriu o catálogo e pesquisou um termo sintético. A tela apresentou “Nenhum material corresponde à busca”. Em seguida, a recepção carregou os indicadores de consultas e caixa. Nenhum material ou permissão foi alterado. Nos dois domínios foram aprovadas 12 verificações de arquivos, saúde, site de agendamento de barbearia e recusa da consulta anônima. O serviço informou prontidão.

## Etapas de vendas da clínica

Em Ajustes da clínica, a titular pode criar uma etapa com nome de até 16 caracteres. As etapas pertencem à clínica e aparecem na ordem de cadastro entre as etapas abertas e os resultados finais. O servidor registra a criação uma única vez, mesmo com envios simultâneos. Também preserva mudanças concorrentes no cadastro da clínica e oculta os nomes internos da resposta compartilhada de onboarding.

Foram aprovados 27 testes com PostgreSQL, 103 testes unitários do módulo e componentes compartilhados, 551 de onboarding e 150 de interface. No Chromium, o formulário foi aberto e enviado pelo teclado em quatro larguras de 320 a 1440 px e nos dois temas. Esta entrega cobre o cadastro de etapas. O registro e a movimentação de oportunidades, o vínculo com paciente e o acompanhamento de orçamentos têm itens próprios na matriz. A verificação desta função em produção permanece pendente.

## Integridade da imagem e coleta de regressões

Ao revisar o pacote destinado às etapas de vendas, dois arquivos validados no repositório estavam ausentes da lista copiada para a imagem. O pacote agora inclui a filtragem dos nomes internos de etapas no onboarding e a verificação da recorrência no esquema 0087. Antes de enviar a imagem, o fluxo confere o conteúdo SHA-256 de quinze arquivos diretamente no contêiner. A conferência local dos arquivos copiados passou e detectou uma divergência introduzida em uma cópia sintética do onboarding. A construção e a execução em contêiner ficam registradas no fluxo de publicação.

A coleta completa do pytest parava porque os testes unitários de encaixe e os testes do navegador tinham o mesmo nome de módulo. O teste de navegador recebeu nome próprio e o fluxo dedicado foi atualizado. A suíte passou a coletar 12.245 casos. Foram executados 55 testes de encaixes e operações de agendamento, quatro fluxos de encaixe no Chromium, 27 integrações odontológicas com PostgreSQL e 606 testes unitários de onboarding, recorrência e preparo da migração. A coleta integral confirma que o executor consegue enumerar a suíte. A aprovação de cada teste depende de sua execução.

## Primeira operação de Vendas

A tela Vendas permite à titular cadastrar uma oportunidade em aberto com título de até 40 caracteres e descrição opcional de até 300. Ela reaparece na listagem da clínica por ordem de criação. O servidor cifra o conteúdo e registra autoria, data e auditoria. Quatro solicitações concorrentes da mesma oportunidade geraram um cadastro. Uma nova tentativa após falha de conexão preservou os campos e a chave do envio. A identificação de pacientes, a atribuição a outro profissional, os rótulos, a movimentação entre etapas e a aprovação de orçamento continuam com cobertura própria na matriz.

A migração 0088 acrescenta uma tabela e um índice. As regras do PostgreSQL verificam a empresa e o vínculo ativo da titular. O suporte, a recepção e outra empresa não conseguiram ler as oportunidades nos testes. O cadastro e a listagem usam as contas e permissões já existentes. O verificador de primeira chave agora considera também o histórico comercial criptografado. Não foram provisionadas chaves ou autorizações em empresas reais.

Passaram 28 testes com PostgreSQL, 692 unitários, 152 de interface e oito combinações de navegador com 320, 390, 820 e 1440 px em claro e escuro. O botão de criação manteve área de toque de pelo menos 44 px nas telas de celular. Um backup sintético em 0087 foi restaurado em um segundo PostgreSQL 16 e preservou identidade, autoria e empresa do paciente. A tentativa de reverter 0088 com uma oportunidade existente foi recusada e os dados foram preservados. A migração e o cadastro de Vendas ainda não foram aplicados em produção.

## Regressão compartilhada da sessão e do catálogo

A simulação do painel executava o controlador real, mas ainda esperava um seletor que permitia trocar de empresa. O painel atual vincula cada conta a uma única empresa. A suíte agora consulta o vínculo confirmado, verifica o isolamento após sair e entrar com outra conta e testa perfis de proprietário, editor e leitor. Uma resposta atrasada da empresa anterior não pode aparecer na próxima sessão. O teste de envio manual também verifica o identificador da operação e uma única requisição. O teste de preço usa o campo monetário real em reais e confere o valor enviado em centavos.

Na base anterior, 19 dos 79 casos falhavam. Após atualizar o ambiente de teste e as expectativas, os 79 passaram com Node 24, sem credenciais nem mensagens reais. Estes ajustes afetam exclusivamente testes. A execução integral do transporte com as dependências do WhatsApp e a imagem Docker tem verificação separada no GitHub.

## Período de criação das oportunidades

A titular pode consultar oportunidades abertas por data inicial, data final ou ambas. O período é inclusivo no calendário da clínica. A lista e as páginas seguintes usam o mesmo filtro. As datas e o fuso são aplicados no PostgreSQL sobre o instante de criação. Uma página pertencente a outro período é recusada. O filtro não altera oportunidades.

Foram verificados os limites da meia-noite, um dia de 23 horas, datas invertidas e limpeza do período. A tela mostra o intervalo aplicado em português e distingue uma busca vazia de uma clínica sem oportunidades. O escopo desta entrega é a lista de oportunidades abertas. Outros estágios e seus totais ainda estão na matriz. A publicação continua dependente das entregas anteriores. O esquema do banco permanece na revisão 0088.

## Destino do Início por nicho

O teste compartilhado de seleção do painel ainda esperava a interface genérica na odontologia. A nova verificação confirma a recepção e seus indicadores reais para a empresa odontológica. Também confirma que uma empresa de estética abre o próprio painel, assinaturas abre a área de assinaturas e só a clínica consulta o serviço de recepção. Os 53 testes conjuntos do espaço odontológico e do Início, incluindo barbearia, passaram localmente. Nenhum arquivo de execução foi alterado nesta correção.

Na revisão anterior das sessões e da idempotência de mensagens, o GitHub confirmou depois os 1.413 testes do conector de WhatsApp. A etapa seguinte do fluxo geral executou 1.797 testes do painel e encontrou 32 falhas. A expectativa antiga do painel odontológico foi uma delas. A aprovação do conector não equivale à aprovação de toda a suíte de interface.

## Busca protegida de oportunidades

A titular pode pesquisar oportunidades abertas pelo título ou pela descrição. O termo é enviado no corpo privado da requisição, não na URL, e não cria um índice em texto aberto. A leitura continua limitada à clínica ativa e à permissão de gestão. A comparação ignora diferenças entre maiúsculas, espaços e acentos, preserva o período escolhido e valida que o cursor pertence ao mesmo resultado.

Os dados sintéticos cobrem paginação, busca em título e descrição, termo inexistente, entrada curta, cursor incompatível, período, papel sem acesso e auditoria. A execução `45102344003` aprovou 30 testes PostgreSQL 16, 104 testes unitários e 153 testes da interface. O navegador percorreu 320, 390, 820 e 1440 px nos temas claro e escuro, validando teclado, foco, ausência de transbordamento e áreas de toque de 44 px. A verificação encontrou e corrigiu antes da aprovação a altura de 36 px do campo em celular e uma espera incorreta do teste pelo botão condicional de limpeza. As regressões compartilhadas `45102344005` e `45102344125` também passaram.

A busca por paciente não foi simulada pelo telefone. Ela permanece separada até que a oportunidade possua o vínculo explícito com o identificador do paciente. Esta entrega não altera o esquema 0088 e não requer migração ou backup de produção. Está no PR 378, commit remoto `8135195d`, ainda sem publicação: o Railway continua no commit `95d3bb76`, e a alteração cancelada `12e76b71-8a64-4874-a4b8-f505ab73db14` não foi repetida.

## Vínculo explícito da oportunidade ao paciente

A criação de uma oportunidade agora possui um seletor opcional de paciente. A titular pesquisa por nome ou telefone e confirma uma identidade individual; o Orion não transforma telefone em identificador. Quando um responsável compartilha o mesmo WhatsApp com mais de um paciente, todos os cadastros correspondentes são apresentados separadamente com nome, nascimento e responsável, e nenhum deles é escolhido automaticamente.

A revisão 0089 é expansiva: adiciona `patient_id` com chave estrangeira composta por clínica e paciente, índice próprio e duas funções de leitura mínima protegidas por `SECURITY DEFINER`. A listagem não expõe telefones nem prontuário. O retrato de identidade que acompanha a oportunidade é criptografado, preservando o nome usado no momento da negociação. Titulares de outra clínica, equipe sem permissão comercial e identificadores estrangeiros são recusados.

Os 55 testes locais da interface odontológica passaram, incluindo repetição após falha, dois pacientes com o mesmo telefone, seleção explícita, escape de conteúdo e regressões do espaço clínico. A validação odontológica `45126028736` aprovou 31 testes com PostgreSQL 16, 105 unitários e 153 de interface. O navegador percorreu 320, 390, 820 e 1440 px em claro e escuro e validou menu, cadeiras, etapas, oportunidades, teclado e a busca assíncrona. As regressões compartilhadas `45126028790` e `45126028447` também passaram.

A primeira execução encontrou uma literal de bytes inválida no teste; a segunda revelou um UUID não serializável no retrato criptografado; a terceira identificou a contagem visual antes da resposta da busca. Os três problemas foram corrigidos antes da aprovação final. A entrega está no PR 379, commit funcional remoto `2d9edbb4`. Ainda não foi implantada: exige backup recuperável antes da migração 0089 e continua atrás da cadeia de publicação já bloqueada. A leitura do Railway em 23/11/2026 confirmou a produção na versão `95d3bb76`, implantação `ba71466c-c0c3-4c64-824d-2c270e4f444d`. A alteração `12e76b71-8a64-4874-a4b8-f505ab73db14` permanece preparada e não foi repetida.

## Busca de oportunidades pelo paciente vinculado

A busca de Vendas agora também considera o nome do paciente que foi explicitamente selecionado na oportunidade. O nome vem do retrato criptografado gravado com a negociação. Telefone, responsável e prontuário não compõem a superfície pesquisável. Assim, duas pessoas que compartilham o mesmo WhatsApp continuam distintas: pesquisar uma delas não retorna a oportunidade vinculada à outra e pesquisar o telefone não infere nenhuma identidade.

A validação `45157893515` aprovou 31 testes com PostgreSQL 16, 106 testes unitários e 153 testes da interface. O navegador percorreu 320, 390, 820 e 1440 px nos temas claro e escuro, incluindo a busca por paciente, teclado, foco e ausência de transbordamento. As regressões `45157893367` e `45157893724` também passaram. O diff do PR foi conferido depois de corrigir uma transferência inicialmente truncada do arquivo grande da interface; o PR 380 terminou com cinco arquivos, 42 adições e oito remoções.

A entrega está no PR 380, commit remoto `c56f17a8`, e não adiciona migração. Continua dependente do vínculo 0089 e da cadeia de Vendas. A produção foi relida após a aprovação externa da imagem de pacientes: o Railway executa o commit `79802e2c`, implantação `de2d1a3b-3398-4c4d-ab9b-22c1dd84dabd`, imagem `aaef00199480a01a526d2cc297f3b79ec41a48d888d342cd018f97e3b07689c7`. A busca pelo paciente ainda não está nessa versão e não foi promovida isoladamente.

## Início do acompanhamento de uma oportunidade

O item VEN-007 passa a oferecer a transição administrativa **Em aberto → Em andamento** em `Painel → Vendas → Oportunidades`. A ação exige a titular com `settings.manage`, envia a versão conhecida e uma chave idempotente e volta a consultar a lista antes de mostrar o novo estado. A API bloqueia versões antigas, clínicas estrangeiras e perfis sem permissão. Cada mudança acrescenta um evento imutável com clínica, oportunidade, versão, origem, destino, autora e horário; não modifica a identidade do paciente vinculado nem usa telefone como identificador.

A migração 0090 é expansiva. Ela acrescenta etapa e versão às oportunidades da revisão 0088 e cria o histórico protegido por RLS. A aplicação recebe apenas leitura e inclusão no histórico e atualização das duas colunas de estado. A reversão é recusada quando existe evento ou oportunidade já movimentada. O publicador exige um backup privado recuperável e reconfere o contrato das revisões 0088 e 0089 antes e depois da cópia. Não foi feita migração em produção nesta etapa.

Passaram localmente 108 testes unitários e 25 testes DOM do espaço odontológico. O cenário PostgreSQL preparado executa quatro pedidos concorrentes com a mesma chave, confirma uma única mudança e um único registro de auditoria, além de testar repetição, conflito, RLS, clínica estrangeira e papel sem autorização. O roteiro visual cobre 320, 390, 820 e 1440 px, claro e escuro, teclado, foco, controles de 44 px e transbordamento. O Chromium local não iniciou porque o ambiente de execução recusou a criação do socket do navegador; por isso, a validação real de navegador e PostgreSQL permanece condicionada ao CI antes da entrega.

A produção foi relida antes do trabalho. O Railway havia substituído a implantação anterior por `97cffbf1-be24-4e1b-b6ce-e795e4e53c56`, imagem `43b1441989261b0b864fd6e1508ffba2a63de7c04ef21475654e6ceb69ae85c0`. Os hashes de `orion-suite.js` e `orion-dental-clinic.js` coincidem exatamente com o commit `79802e2c`; portanto, a nova imagem repete a versão publicada de pacientes e ainda não contém Vendas. Não havia alteração preparada no Railway. Nenhum deploy foi solicitado, pois 0090 depende da promoção ordenada de 0088 e 0089 e de backup recuperável.
