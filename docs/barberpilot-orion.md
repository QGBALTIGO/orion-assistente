# BarberPilot → Orion: referência, desenho e implementação

Levantamento realizado em 17–18 de outubro de 2026. A implementação parte do
Orion em `3f171babc4e7e5d0736442403bfdb49dab513264` e preserva as correções de
identidade e recuperação de reservas incorporadas pelo PR #52.

O resultado é uma suíte de gestão integrada ao painel e ao banco do Orion.
O pedido de fidelidade abrange estrutura, textos funcionais, estados, cores,
medidas e comportamento responsivo. A equivalência visual integral ainda
depende de comparação da versão implantada, e as diferenças funcionais estão
explicitadas na [matriz de cobertura](barberpilot-functional-coverage.md).

## Fontes e método

Foram combinadas três formas de evidência:

1. Inspeção da aba autenticada de [BarberPilot](https://barberpilot.app/app/dashboard),
   com navegação por áreas, abas, filtros, formulários e diálogos, sem salvar
   alterações nos dados da conta de referência.
2. Leitura do DOM renderizado, estilos computados e retângulos de elementos,
   nas larguras de celular, tablet e desktop abaixo.
3. Análise estática de JavaScript e CSS públicos usando o
   [REA](https://github.com/morluto/rea), complementada por extração de componentes
   e literais via AST. O JavaScript do BarberPilot foi tratado como texto.

O levantamento produziu **103 registros de telas ou estados de interface** e
um inventário de assets. Há 47 registros com sufixo desktop, 28 referentes a
mobile e 28 a tablet. Esses números incluem modais, abas e estados diferentes;
não representam 103 páginas independentes nem 103 testes concluídos.
Capturas autenticadas, dados pessoais da conta e bundles de terceiros não fazem
parte deste repositório.

### REA realmente executado

Versão utilizada: **6.5.0**, commit
[`2fb5e204a35723f6f843d56ca47b0db94d8fcc44`](https://github.com/morluto/rea/tree/2fb5e204a35723f6f843d56ca47b0db94d8fcc44),
licença MIT. Instalação isolada, sem modificar o navegador ou a sessão do usuário.

| Execução | Arquivos JS | Nós AST visitados | Achados heurísticos | Falhas de parse |
|---|---:|---:|---:|---:|
| Inventário inicialmente observado | 162 | 412.865 | 4.258 | 0 |
| Seleção central | 5 | 32.078 | 437 | 0 |
| Sete módulos de negócio | 7 | 68.873 | 582 | 0 |

As seleções têm interseção: seus totais não devem ser somados como cobertura
única. A primeira execução atingiu o orçamento de expansão do grafo em parte
dos arquivos; as duas execuções focadas concluíram dentro do orçamento. Um
achado heurístico não comprova uma vulnerabilidade, uma regra de servidor nem
um comportamento executado.

Foram inventariados 198 assets públicos: 197 JavaScripts e um CSS. Desses,
163 foram observados inicialmente e 35 vieram de caminhos literais de imports
e preloads. Isso não significa que o REA analisou os 197 JS em uma única execução.
A extração complementar registrou 19 conjuntos de evidências por módulo, com
URL, hash SHA256, linha, coluna e offsets UTF-16.

O shell autenticado correto era `app-BwudNkCd.js`. O arquivo
`PanelChrome-C9IB_8-Z.js` correspondia a outro painel administrativo e não foi
usado como modelo da navegação da barbearia.

### Dimensões de referência

| Perfil | Viewport configurado | O que foi observado |
|---|---|---|
| Celular | 390 × 844 CSS px | Menu móvel, cartões empilhados, abas horizontais, formulários e wizard |
| Tablet | 820 × 1.180 CSS px | Navegação lateral recolhida, reordenação de grades e formulários |
| Desktop | 1.440 × 1.000 CSS px | Barra lateral expandida, grades, tabelas, modais e conteúdo completo |

Foram redimensionamentos do Chrome conectado. Não equivalem a testes em
aparelhos físicos, Safari/iOS, teclado virtual ou interação por toque. Em
algumas capturas de 390 px a área útil tinha 375 px devido à barra de rolagem.
Medidas de conteúdo devem considerar esse desconto antes de comparar posições.

## Sistema visual

### Cores identificadas

| Papel | Tema claro | Tema escuro declarado na referência |
|---|---|---|
| Primária | `#2e7bf6` | `#2e7bf6` |
| Fundo | `#f5f8fc` | `#05080f` |
| Cartão | `#ffffff` | `#0d1320` |
| Texto principal | `#0f1b33` | `#eaf0f7` |
| Texto secundário | `#4e5a72` | `#8a97ac` |
| Borda | `#dce3ee` | `#1b2437` |
| Superfície discreta clara | `#e9edf3` | — |
| Sucesso claro | `#16a34a` | — |
| Alerta claro | `#b8860b` | — |
| Erro claro | `#c1272d` | — |

O tema claro foi observado na interface. Os valores escuros vieram do CSS
publicado; isso não constitui uma comparação visual aprovada do tema escuro.
O site público possui cor configurável por empresa, independente da primária
da aplicação. O Orion conserva a escolha de tema claro, escuro ou do sistema.

### Tipografia e geometria

| Elemento | Medida ou comportamento da referência |
|---|---|
| Corpo | Figtree, com fallback para fontes do sistema |
| Títulos | Outfit |
| Pesos das fontes variáveis | Figtree 300–900; Outfit 100–900 |
| Sidebar desktop | 256 px expandida; 64 px recolhida |
| Drawer móvel | 288 px |
| Corte da navegação desktop | 768 px |
| Estado inicial intermediário | Recolhido abaixo de 1.024 px quando não há preferência salva |
| Cabeçalho | 56 px, sticky, padding horizontal de 24 px, fundo translúcido |
| Conteúdo principal | Limite de 1.280 px; padding de 16 → 24 → 40 px |
| Título principal | 24/32 px no tamanho menor; 30/36 px no maior; peso 700 |
| Ações rápidas | Uma coluna → três a partir de 640 px; ícone em caixa de 48 px |
| Modal de agendamento | Aproximadamente 512 px no desktop; ocupa a largura móvel |
| Sidebar no documento | Acompanha a altura do documento; não é um painel desktop limitado a 100vh |
| Transição da sidebar | 300 ms |
| Drawer | Abertura 500 ms; fechamento 300 ms |
| Agenda | Faixa horária de 70 px; modo tablet de 110 px |
| Site público | Container de 896 px; capa de 224/288/384 px conforme largura |

Breakpoints estáticos publicados: 40, 48, 64, 80 e 96 rem, equivalentes a
640, 768, 1.024, 1.280 e 1.536 px com raiz de 16 px. A equivalência não dispensa
a conferência do layout efetivamente renderizado.

As fontes são distribuídas localmente, acompanhadas das licenças SIL OFL:
[Figtree](../app/static/figtree-OFL.txt) e
[Outfit](../app/static/outfit-OFL.txt). A origem oficial é o repositório
[Google Fonts](https://github.com/google/fonts), diretórios `ofl/figtree` e
`ofl/outfit`. Não foram incorporados logotipo, identidade ou dados de clientes
do BarberPilot ao Orion.

## Organização da implementação

| Camada | Arquivos principais | Responsabilidade |
|---|---|---|
| Shell e telas | [orion-suite.js](../app/static/orion-suite.js), [orion-suite.css](../app/static/orion-suite.css) | Navegação, estado por empresa, telas de gestão, formulários, tema e responsividade |
| Ponte com o painel existente | [panel.html](../app/static/panel.html) | Login, contas, seleção de empresa, WhatsApp, áudio, catálogo comercial e configuração do atendimento |
| Gestão | [management_api.py](../app/management_api.py), [management_models.py](../app/management_models.py), [management_schemas.py](../app/management_schemas.py) | Clientes, indicadores, lançamentos, relatórios, metas, preferências do site e transições de atendimento |
| Agenda recorrente | [management_blocks.py](../app/management_blocks.py) | Expansão de séries, conflitos e remoção de uma ocorrência, próximas ou série inteira |
| Combos e adicionais | [service_packages_api.py](../app/service_packages_api.py) | Preço, duração, profissionais elegíveis, variantes internas e snapshots |
| Fidelização | [loyalty_rewards.py](../app/loyalty_rewards.py) | Pontos automáticos, cupons, descontos e histórico |
| Quitações | [financial_settlements.py](../app/financial_settlements.py) | Comissão acumulada, vales, compensações parciais, revisão e recibo persistente |
| IA de gestão | [management_ai.py](../app/management_ai.py) | Campanhas revisáveis, consultor, ideias de fidelização, quota e resultado estruturado |
| Equipe | [management_team.py](../app/management_team.py) | Convite, aceite, aprovação do proprietário e alteração de acessos |
| Operação | [management_preferences.py](../app/management_preferences.py) | Preferências, automações em rascunho, fila Meta observável e notificações internas |
| Site e agendamento público | [public_site_api.py](../app/public_site_api.py), [orion-public.js](../app/static/orion-public.js), [orion-public.css](../app/static/orion-public.css) | Publicação explícita, catálogo público, disponibilidade e confirmação |
| Convite | [orion-invite.js](../app/static/orion-invite.js) | Fluxo de conta existente, retorno do login e aceite manual |
| Rotas e distribuição | [main.py](../app/main.py), [pyproject.toml](../pyproject.toml) | Inclusão de routers, arquivos permitidos e fontes no pacote |

As rotas específicas precedem o router genérico do catálogo. Essa ordem é
necessária para que, por exemplo, `PUT .../management/site` alcance a configuração
do site em vez de ser interpretado como um UUID pelo catálogo legado.

### Integração com o Orion existente

A nova navegação usa os mesmos usuários, empresas, clientes, serviços,
profissionais e reservas. Os comandos existentes de conexão WhatsApp, conversas,
simulador de texto/áudio, catálogo comercial, pedidos e versões de atendimento
continuam acessíveis. A suíte não cria um banco paralelo nem dados demonstrativos
em contas reais.

Trocar de empresa invalida respostas antigas; sair da conta descarta estado
da suíte. Edições pendentes usam os guardas do painel. Uma resposta de outra
empresa ou de uma sessão anterior não pode preencher a tela atual. O cliente
HTTP também aceita respostas `204`, usadas por exclusões sem corpo JSON.

As permissões reais continuam sendo `owner`, `editor` e `viewer`, com a fronteira
de suporte já existente no Orion. O modelo de treze permissões de módulos
declarado pelo BarberPilot não foi transplantado implicitamente.

### Decisões de comportamento verificáveis

- O dashboard distingue fatos históricos de indicadores do período. Missões
  usam existência de confirmação, configuração efetiva de agenda e quantidade
  de clientes ativos com telefone; não ficam concluídas por um contador fictício.
- Clientes exigem nome no cadastro; telefone continua necessário quando a
  operação de reserva depende da identidade WhatsApp do motor atual.
- Reservas usam a disponibilidade, os locks e a exclusão de horários do Orion.
  Profissional inativo ou que não aceita agendamento fica inelegível.
- Combos usam a interseção dos profissionais capazes de executar seus
  componentes. A composição, o preço e a duração históricos são preservados.
- Conclusão registra valor bruto, desconto, valor líquido e comissão. Alterar
  comissão ou catálogo depois não recalcula silenciosamente o passado.
- Cupom é validado e consumido na mesma transação da conclusão. Pontos e
  recompensas não são duplicados por repetição da mesma operação.
- A quitação exige a revisão de um snapshot atualizado. Dois pedidos
  concorrentes não podem quitar a mesma comissão duas vezes.
- Um convite aceito permanece aguardando aprovação do proprietário. O link
  usa um token aleatório cujo hash é armazenado; a aplicação não envia e-mail
  automaticamente. O retorno após login também não aceita o convite sozinho.
- Configuração salva do site e versão pública são estados distintos. Apenas
  a publicação explícita pelo proprietário troca o snapshot público.
- A geração de IA depende de habilitação e credencial válidas e de uma ação
  explícita na interface. Sua saída é validada; o modelo não recebe ferramentas
  para executar pagamentos, alterar cadastros ou enviar mensagens.

## Verificação e limites de aceite

A [matriz funcional](barberpilot-functional-coverage.md) descreve cada módulo,
incluindo funcionalidades observadas que ainda não têm equivalente completo.
O [runbook](management-suite-runbook.md) registra migrations, configuração,
comandos de testes e critérios de implantação.

Os testes de banco usam PostgreSQL 16 real, três papéis distintos e RLS forçada.
Redis real valida os limites atômicos. Dados de testes são sintéticos. Chamadas
de IA e transportes de mensagens são simulados nos testes para validar seus
contratos sem cobrar nem contatar clientes reais.

Ainda é necessário comparar a versão implantada do Orion nas três larguras.
O navegador conectado bloqueou a abertura de uma prévia local por protocolo
`data:`; não foi usada uma rota alternativa para contornar esse bloqueio.
Testes DOM verificam comportamento, mas não comprovam pixels, quebras de linha,
fontes renderizadas, rolagem ou experiência em aparelho físico.

No site público da referência, os dias consultados não ofereceram horário;
a terceira etapa de confirmação não pôde ser executada. Não houve criação
de agendamento real, envio de mensagem, cobrança, convite de outra pessoa,
alteração de senha ou mudança de permissão na conta do BarberPilot.

Por isso, esta entrega não declara que cada pixel ou toda regra privada do
BarberPilot está reproduzida. Ela registra o que foi observado, o que foi
implementado e quais comprovações permanecem necessárias.
