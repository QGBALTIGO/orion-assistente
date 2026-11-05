# Aquecimento — validação e publicação

## Escopo da revisão

Base de produção: `32a90c0fd003c5fbac70c1bd541bf9ce20171040`, PR 263.
O delta funcional adiciona a autorização do guia, a rota e o artigo nativo
do Orion. Não muda banco, processamento, envio, transcrição, ponte ou
infraestrutura. O [contrato observado](warmup-observation.md) descreve a
metodologia manual e os limites das afirmações da referência.

## Verificações locais concluídas

| Verificação | Resultado | O que comprova |
|---|---|---|
| Novos casos de política de workspace | 9 aprovados | Empresa ativa de assinaturas com/sem plano comercial; empresa pausada/suspensa/cancelada; demais nichos em quatro estados de plano |
| DOM de orientação, revisão final | 14 aprovados | Rota, proprietário, perfil, estado ativo, concessão, revogação, conteúdo, tabela, links e ausência de mutações |
| DOM de workspace e orientação, revisão anterior de conteúdo | 29 aprovados | Integração da navegação existente; não somado como execução da revisão final |
| Chromium local, revisão final | 9 aprovados | Seis combinações de viewport/tema e três limites de acesso |
| Sintaxe e patch | Aprovados | JavaScript parseável, YAML válido e `git diff --check` sem erro |

O browser usa o painel e os assets reais, com servidor de dados sintéticos
em loopback. Os viewports são 390×844, 768×1024 e 1440×900, nos temas claro
e escuro. Foram verificados quatro passos, tabela de contatos, legenda,
qualificação dos exemplos, links oficiais e ausência de overflow.
No celular e no computador, o atalho abre a lista vazia de Dispositivos e
volta ao guia. Os casos de perfil comum e empresa inativa mantêm uma
concessão sintética antiga propositalmente, para verificar o bloqueio
independente do contexto. Não ocorreram pedidos externos, erros de página
ou requisições diferentes de GET. Capturas de celular escuro e computador
claro foram inspecionadas visualmente.

São testes sintéticos e emulação de viewport; não representam aparelhos
físicos, entregas de mensagens nem uma prova de reputação do número.

## Qualificação e publicação

Os seis grupos preexistentes do workflow `subscription-chatbot.yml` foram
preservados. Os novos casos DOM e browser foram incluídos; o unit de
workspace já integrava o grupo de API. Nenhum novo gate de migração,
PostgreSQL ou ponte foi criado para este artigo estático.

A qualificação do candidato exato, sua árvore, os resultados de CI, os
deployments e a conferência ao vivo ainda estão em andamento nesta revisão.
O registro final será acrescentado à PR e ao histórico de continuidade
somente após observação. Não se declara o repositório inteiro aprovado.

As correções documentais que acompanham este módulo registram a conclusão
já observada da etapa 11 e a pendência da carteira e recargas da etapa 12.
Não alteram o código funcional dessas etapas.
