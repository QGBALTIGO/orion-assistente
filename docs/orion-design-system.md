# Orion Design System — contrato visual

Referência: componentes existentes e aprovados de Barbearia, especialmente
os filtros de Clientes e da administração. A plataforma tem uma identidade;
nichos variam conteúdo e permissões, não inventam outro visual.

## Componentes oficiais

| Componente | Contrato |
| --- | --- |
| Tokens | Usar variáveis semânticas --os-bg, --os-card, --os-text, --os-muted, --os-border, --os-primary, --os-soft, --os-tint, --os-font e --os-display. |
| Seleção exclusiva, abas e filtros | os-pill-tabs + os-pill-tab; os-ui-segments marca a migração explícita de um componente legado. Um único grupo horizontal, sem quebra em duas linhas; scroll interno quando necessário. aria-selected ou aria-pressed conforme semântica. |
| Cabeçalho | os-title-row; os-ui-heading para módulos migrados; título, descrição e ações alinhados, sem estilos próprios por página. |
| Ações | os-actions, os-button, os-outline, os-ghost, os-danger, os-small, os-large; um CTA prioritário por contexto; editar/atualizar como ações secundárias, exclusão com confirmação. |
| Formulários | os-form, os-field, os-form-actions e OrionFields. Labels visíveis, máscaras e feedback já existentes. |
| Superfícies | os-card, os-card-header, os-empty, os-notice, os-badge. Usar os tokens de cor e espaçamento. |
| Navegação | Sidebar, dock e Perfil compartilhados; não recriar menu, rota ou permissões em módulos. |
| Responsividade | 320, 430, 768, 1024, 1440 px; claro/escuro; nenhum overflow horizontal da página; somente grupos/listas explicitamente roláveis. |
| Acessibilidade | Nome acessível, estado selecionado sem depender só de cor, foco visível, teclado, alvo de toque suficiente e feedback real. |

## Regra para desenvolvimento futuro
Antes de implementar uma tela, procurar o componente equivalente no painel
principal. Reusar classes e funções de UI existentes; se faltar um componente,
defini-lo primeiro na camada compartilhada. CSS de nicho descreve apenas
layout específico de conteúdo, não redefine a aparência de controles.
Não criar variantes visuais para resolver uma única tela. Não mover ações
de negócio, alterar permissões, criar novas rotas ou trocar fluxos durante
uma migração de estilo sem pedido explícito. Registrar e justificar exceções.
Executar o auditor de interface e a bateria de navegadores antes de publicar.

## Primeira migração
Minha conta nos quatro nichos; os sete grupos de seleção conhecidos no
Dashboard, Ofertas, Automações e Fila e histórico de Afiliados; cabeçalhos
e ações dos módulos nativos de Afiliados. As rotas de encurtamento, autenticação
e dados permanecem inalteradas. As demais interfaces já usam classes do
suite em graus diferentes; o relatório de inventário lista a dívida restante.
A revisão total de todas as telas e estados é uma etapa incremental,
não está implicitamente concluída por esta primeira migração.

## Implementação
orion-design-system.css é a camada compartilhada; o script de empacotamento
instala a camada no stylesheet de produção e migra marcação por âncoras.
O verificador de contrato e testes do navegador bloqueiam regressões dos
componentes migrados. O inventário é uma lista de candidatos à revisão,
não uma prova de que todo seletor específico seja um problema.
