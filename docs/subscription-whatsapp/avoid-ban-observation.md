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

A implementação e as verificações focadas foram concluídas antes da
publicação. A etapa 15 só começou após a conferência ao vivo registrada
a seguir.

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
A candidata, a publicação e a conferência real estão registradas a seguir.


## Publicação e conferência ao vivo

A [PR 266](https://github.com/QGBALTIGO/orion-assistente/pull/266) publicou
`f33b373ea0273296d0dc7b8e02c75353a9b648cc`, com a mesma árvore
`df08ad4824580d7a8f1988279269f64e756be7f7` da candidata
`f9b4827358c33323bb85a241551ce98123007127`. A liberação usou o critério
focado descrito acima; os resultados completos de CI automático eram
pendentes e não foram apresentados como aprovados.

API `3361e1a1-4ff0-4856-9790-0e948a23c606` e worker
`e1a4f766-054b-4f48-87a0-5f1fc3465a1e` confirmaram o merge, SUCCESS e
online 1/1, com barreiras 0071. Os cinco serviços estavam saudáveis no
corte de 17:31:37 UTC, sem alertas nem operações pendentes. A ponte
permaneceu compatível na revisão anterior, com a nova tentativa ignorada
pelos watchpaths; não houve mudança de schema ou infraestrutura.

**Conferência no Chrome conectado concluída até 17:33:25 UTC:** o menu
Evitar Banimento apareceu na empresa real sem plano comercial; o artigo
exibiu as dez orientações e exemplos; os três atalhos abriram Dispositivos,
Aquecimento e Diagnósticos, retornando ao guia a cada vez. O console não
registrou erros, e a captura confirmou o visual nativo no tema escuro.
Foram apenas leituras e navegação, sem executar diagnóstico, conectar
número, alterar configuração ou enviar mensagens.

A etapa 14 foi encerrada após essa conferência. A observação de Resolver
Banimento começou somente em seguida.
# Adendo de CI — 2026-11-05, 17:58:01 UTC

Os seis grupos dedicados da candidata `f9b4827358c33323bb85a241551ce98123007127`
encerraram com sucesso no [run 42074943585](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42074943585):
2.768 API, 588 PostgreSQL, 579 DOM, 488 ponte, 139 Chromium e 139 WebKit,
totalizando **4.701 execuções aprovadas** na mesma árvore. Esse resultado
posterior não altera a cronologia da publicação focada registrada abaixo.

A suíte geral terminou com 37 jobs aprovados e 13 falhos. O FATIA, pela
primeira vez completo desde a etapa 11, revelou 21 falhas adicionais na
fixture de API de Enviar Mensagem: o registro anterior de `app.main` aplicava
a guarda real sobre um ator sintético incompleto. As 212 falhas/erros da
última base completa permaneceram. A reprodução e a correção isolada dessa
fixture, sem retirar asserções ou guardas do produto, estão no
[registro específico](direct-api-fixture-validation.md). O CI dessa candidata
continua registrado como falho na suíte geral; a correção é posterior.
