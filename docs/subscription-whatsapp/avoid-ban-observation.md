# Evitar Banimento — observação, adaptação e validação

## Referência acessível

Em 2026-11-05, depois do encerramento de Aquecimento no Chrome real,
foi aberta a página gratuita
<https://botbot.chat/user/whatsapp/avoid-ban>. É um artigo com dez
orientações e três atalhos. Não há formulário, configuração, execução
automática, cobrança ou serviço que garanta evitar bloqueios.

Os três atalhos foram exercitados no Chrome conectado, com retorno ao
artigo após cada leitura:

| Atalho observado | Destino alcançado | Resultado |
|---|---|---|
| Meus Dispositivos | `/user/device` | Página Dispositivo acessível |
| Aquecimento do número | `/user/whatsapp/warmup` | Guia de aquecimento acessível |
| Diagnóstico | `/user/diagnostics` | Página Diagnósticos acessível |

Nenhuma operação dos módulos de destino foi iniciada. O console não
registrou erros nessa navegação. Não houve envio, compra, desconexão ou
alteração de configuração.

## Estrutura e precisão das orientações

O Orion preserva os dez temas na ordem observada, as orientações de uso
manual, o exemplo de cadência e os três atalhos. Usa redação própria e o
artigo de ajuda já existente no painel.

| Tema observado | Tratamento no Orion |
|---|---|
| Ferramentas conectadas | Revisar sistemas e permissões; atalho para Dispositivos |
| Autorização do contato | Pedir permissão antes da conversa e respeitar o encerramento; exemplo original de pergunta |
| Personalização | Usar contexto verdadeiro e detalhes pertinentes à conversa |
| Números novos | Preparação manual e atalho para Aquecimento |
| Saúde do número | Acompanhar respostas, atrasos e estado técnico; atalho para Diagnósticos |
| Palavras relacionadas a cobrança e promoção | Explicar o contexto de mensagens com Pix, boleto ou promoção, sem inventar lista de palavras proibidas |
| Conta restringida | Tratar avisos pelos canais oficiais, mantendo identificação verdadeira |
| Cadência de contato | Saudação, eventual acompanhamento após algumas horas e encerramento no dia seguinte como exemplo condicionado à autorização, necessidade e resposta |
| Períodos sensíveis | Revisar pertinência e tom da comunicação em crises ou grandes acontecimentos |
| Organização dos números | Planejar atendimento e campanhas dentro da capacidade disponível, com números autorizados e sem obrigar a criação de um segundo dispositivo |

A referência também faz afirmações sobre exclusividade de um painel,
filtros de palavras, reconhecimento de foto/identidade e rigor automático
em determinados períodos. As fontes oficiais consultadas não estabelecem
essas alegações ou uma garantia de imunidade a bloqueios. O Orion não as
apresenta como fatos nem orienta trocar a identidade para contornar uma
restrição. O roteiro mantém sua finalidade informativa e oferece
encaminhamento às regras oficiais.

Fontes oficiais consultadas nesta sessão:

- [Política de mensagens do WhatsApp Business](https://whatsappbusiness.com/policy/): identificação verdadeira, autorização e respeito à interrupção do contato.
- [Diretrizes de mensagens do WhatsApp](https://www.whatsapp.com/legal/messaging-guidelines): mensagens indesejadas, spam, avisos e restrições.

Os links oficiais são estáticos. Nenhum site externo é consultado
automaticamente pelo artigo. A cadência é texto explicativo, sem criar
agendamento ou disparo.

## Contrato Orion e critério de publicação

Rota: `/painel?aba=evitar-banimento`. A permissão existente
`whatsapp_guidance.view` é combinada com proprietário, workspace
`subscription_services` e **status ativo da empresa**, conforme a correção
da PR 265. O status comercial do plano não é requisito para este guia.
Cada atalho conserva a permissão do módulo de destino.

São usados os artigos, botões, listas, ícones e cores nativos do Orion.
Não há nova fonte, CSS, dependência, API, migração, provedor ou transporte.
O escopo funcional é um artigo e sua navegação.

A publicação exige revisão do conteúdo observado, validação focada da
nova rota e seus limites de acesso, empresa ativa sem plano comercial,
atalhos, ausência de mutações e layout em celular/tablet/computador nos
dois temas, seguida de conferência no Chrome real. Os workflows gerais
automáticos serão registrados separadamente; resultados pendentes não
serão apresentados como aprovações. Não se exige repetir testes de banco
ou transporte inalterados como condição artificial para este artigo.

A implementação e as verificações focadas estão concluídas; a publicação
está em preparação. A etapa 15 só
começa após a publicação e a conferência ao vivo desta etapa.

## Verificação focada concluída

A revisão final passou em **19 testes DOM** (15 cenários anteriores e
quatro novos) e **nove Chromium** em 16,05 segundos. A execução de browser
selecionou somente os casos de Evitar Banimento; os 27 casos fora do foco
foram desmarcados, sem serem contados como aprovações.

Foram conferidos os seis layouts 390×844, 768×1024 e 1440×900 nos temas
claro/escuro, sempre com empresa ativa sem plano comercial. Os três
atalhos foram exercitados no celular e no computador, incluindo leitura
da lista vazia de Diagnósticos e retorno ao artigo. Os três limites de
acesso passaram. Os nove relatórios tiveram zero mutações, pedidos
externos ou erros de página. Capturas de celular escuro e computador
claro foram inspecionadas, sem defeito visual material. Sintaxe JavaScript
e `git diff --check` aprovados.

São dados sintéticos e emulação de viewport, sem contatos ou envios reais.
A candidata e a publicação serão registradas na PR, e a conferência real
será acrescentada após a atualização dos serviços.
