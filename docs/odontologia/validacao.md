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

A revisão 3f71cfe5 passou nos jobs clínicos e na geração de imagem do GitHub Actions 45076681302. O Railway preparou a troca de imagem do serviço da API e a ferramenta de aprovação registrou que a ação não foi aprovada. A criação da imagem sozinha não confirma publicação. Não houve nova concessão de acesso para testar a ficha.

No CI geral, a base anterior 29573ae e 3f71cfe5 apresentaram as mesmas 19 falhas de interface do simulador e a mesma falta da dependência Playwright na coleta de três testes Python. Os testes clínicos, o ensaio de restauração e os testes específicos de regressão foram executados separadamente. O CI geral permanece pendente de correção.

## Catálogo de materiais

O menu Estoque abre um espaço de materiais dedicado. A busca, a ordenação alfabética e a paginação são feitas no banco para a clínica autorizada. A lista mostra os saldos reais e abre o histórico, a entrada e a saída do material selecionado. Se uma operação foi gravada e a resposta se perdeu, repetir o envio a partir do formulário conserva a chave original. O acesso usa as autorizações já existentes. A página dedicada não requisita a lista de pacientes nem seus registros clínicos.

A referência financeira também foi verificada em Fluxo de Caixa → Filtrar. Foram identificadas combinações de movimentação, situação de pagamento, nota fiscal, convênio, recorrência, recibo, meio de pagamento, conta, categoria e filtros salvos. As operações estão registradas individualmente na matriz.
