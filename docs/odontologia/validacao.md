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
