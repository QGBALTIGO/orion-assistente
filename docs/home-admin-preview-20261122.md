# Início — prévia restrita ao administrador

Base: 52ab2e70e5e6bbe74ae543f7652132eaf150b8d2. Os hashes dos dois arquivos da interface foram comparados com o domínio público antes da alteração e coincidem com o runtime publicado.
Imagem de base: `sha256:d6ae62c9f013e999ca892826087c2ab44641770e3daedaa41865d5557b552b09`.

A imagem acrescenta somente `orion-suite.js` e `orion-suite.css`. Não copia backend, HTML, migrations ou configurações. A agenda completa, demais rotas e shell continuam na versão atual.

O novo Início usa a identidade administrativa autenticada (`platformWorkspace`), excluindo suporte, membros, proprietários de empresas e modo manutenção. URL e armazenamento local não habilitam a prévia. O dashboard anterior segue sendo o caminho normal dos demais usuários. A liberação geral exige uma alteração posterior e não está habilitada aqui.

Dados reais: faturamento de atendimentos concluídos, quantidade concluída/total, clientes ativos cadastrados, confirmados e ticket médio. Clientes cadastrados é uma contagem da empresa, não clientes atendidos no dia. O gráfico mantém os últimos sete dias como contexto e destaca o período escolhido; as barras vêm de `/performance/daily`. A lista da agenda continua explicitamente no dia atual da empresa. Erros e dados incompletos não são convertidos em zero. O ticket usa arredondamento ao centavo, enquanto o faturamento usa formatação de centavos inteiros segura.

Verificação local: 18 casos específicos de identidade, datas, falhas, resposta tardia, valores grandes e escaping. Regressão focada: 160 casos passando, incluindo navegação, financeiro, agenda e os layouts existentes de barbearias/assinaturas. A revisão visual usa dados sintéticos, nos temas claro/escuro e larguras 320, 390, 768 e 1024px, além do desktop.

Um teste legado de `dashboard-today-preview.test.mjs` procura o botão `agenda-next` removido anteriormente da visão diária. A mesma falha foi reproduzida na base publicada sem estas mudanças. A alteração não reintroduz esse controle na agenda nem modifica o teste antigo.

Publicação: aguardar o workflow Home admin-only preview concluir, usar o digest imutável publicado no serviço API atual e conferir SUCCESS e os hashes dos assets públicos. Não alterar worker, QR bridge ou dados.


## Revisão após avaliação do administrador

Remove a rotação e a sombra deslocada dos ícones dos indicadores; usa uma grade para alinhar títulos, valores e ícones. Mantém um único botão Novo agendamento no cabeçalho e somente Novo cliente/Meu site nos atalhos. O estado vazio da agenda agora é compacto, sem ação de criação repetida nem atualização manual.

A prévia consulta indicadores, gráfico, metas e agenda automaticamente a cada cinco segundos enquanto a página está visível. As quatro respostas são aplicadas na mesma passagem, sem remontar a página. Leituras sobrepostas são evitadas; foco, rolagem e formulários permanecem intactos. Falhas preservam os últimos valores confirmados, identificam reconexão e usam intervalo progressivo. O retorno à página e gravações pedem uma leitura imediata; se uma leitura já estiver em andamento, a nova consulta fica pendente para logo após ela. Respostas de períodos, empresas ou telas antigos são descartadas. O temporizador é removido ao sair do Início ou encerrar a sessão.

O formulário aberto a partir deste Início recebe cabeçalho fixo, corpo com rolagem e rodapé separado. Os campos de data/hora têm largura limitada para evitar o transbordamento observado no iPhone. Tamanhos de fonte dos campos evitam zoom involuntário no Safari; serviços e espaçamentos ficam mais compactos. Essa apresentação também permanece limitada ao piloto; a agenda e os formulários das contas comuns mantêm sua versão publicada.

Validação adicional: atualização em segundo plano, respostas concorrentes, reconexão, pausa em segundo plano, saída/desmontagem, preservação dos dados digitados e atualização após gravar. Verificação visual em 320, 390, 768 e 1024 px, além de desktop, inclui tema escuro, estados vazios e formulário com serviços. Nenhuma conta real ou agendamento de produção foi criado nos testes. O navegador de produção estava sem sessão; a publicação deve ser confirmada pelo Railway e pelos hashes dos dois arquivos no domínio público.
