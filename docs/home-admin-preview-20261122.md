# Início — prévia restrita ao administrador

Base: 52ab2e70e5e6bbe74ae543f7652132eaf150b8d2. Os hashes dos dois arquivos da interface foram comparados com o domínio público antes da alteração e coincidem com o runtime publicado.
Imagem de base: `sha256:d6ae62c9f013e999ca892826087c2ab44641770e3daedaa41865d5557b552b09`.

A imagem acrescenta somente `orion-suite.js` e `orion-suite.css`. Não copia backend, HTML, migrations ou configurações. A agenda completa, demais rotas e shell continuam na versão atual.

O novo Início usa a identidade administrativa autenticada (`platformWorkspace`), excluindo suporte, membros, proprietários de empresas e modo manutenção. URL e armazenamento local não habilitam a prévia. O dashboard anterior segue sendo o caminho normal dos demais usuários. A liberação geral exige uma alteração posterior e não está habilitada aqui.

Dados reais: faturamento de atendimentos concluídos, quantidade concluída/total, clientes ativos cadastrados, confirmados e ticket médio. Clientes cadastrados é uma contagem da empresa, não clientes atendidos no dia. O gráfico mantém os últimos sete dias como contexto e destaca o período escolhido; as barras vêm de `/performance/daily`. A lista da agenda continua explicitamente no dia atual da empresa. Erros e dados incompletos não são convertidos em zero. O ticket usa arredondamento ao centavo, enquanto o faturamento usa formatação de centavos inteiros segura.

Verificação local: nove casos novos de identidade, datas, falhas, resposta tardia, valores grandes e escaping. Regressão focada: 131 casos passando, incluindo navegação, financeiro, agenda e os layouts existentes de barbearias/assinaturas. A revisão visual usa dados sintéticos, nos temas claro/escuro e larguras 320, 390, 768 e 1024px, além do desktop.

Um teste legado de `dashboard-today-preview.test.mjs` procura o botão `agenda-next` removido anteriormente da visão diária. A mesma falha foi reproduzida na base publicada sem estas mudanças. A alteração não reintroduz esse controle na agenda nem modifica o teste antigo.

Publicação: aguardar o workflow Home admin-only preview concluir, usar o digest imutável publicado no serviço API atual e conferir SUCCESS e os hashes dos assets públicos. Não alterar worker, QR bridge ou dados.
