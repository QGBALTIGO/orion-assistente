# Landing Orion: direção e entrega

## Objetivo

Reconstruir a página pública do Orion com a identidade do painel e uma
apresentação adequada a diferentes negócios. A publicação substitui somente a
landing existente e acrescenta a captação de solicitações, com acompanhamento
restrito à equipe global de suporte/admin em Configurações.

Endereço público: https://orion-assistente-production.up.railway.app/

Acesso ao produto: https://orion-assistente-production.up.railway.app/panel

## Identidade e composição

- Marca oficial e fontes locais do painel: Outfit nos títulos e Figtree no texto.
- Fundo principal `#f5f8fc`, texto `#0f1b33`, divisores `#dce3ee` e azul do
  produto `#2e7bf6` nos destaques. Botões com texto branco usam `#2162d0`.
- Seções escuras apresentam o produto e o contato. Fotografias mostram pessoas
  trabalhando, sem apresentar modelos de banco de imagens como clientes Orion.
- Uma história liga atendimento, agenda, cadastro e gestão. Os nomes, horários,
  valores e gráficos da demonstração são fictícios e estão identificados.
- Recursos e planos seguem grades contínuas, com divisores e hierarquia clara.
  A página não depende de cartões soltos, depoimentos inventados, números de
  clientes ou promessas de resultado financeiro.
- Ícones SVG compartilham traço e proporções. As animações indicam mudança de
  conteúdo, foco e interação, sem ocupar a página com efeitos decorativos.

O contraste nominal entre branco e o azul dos botões é aproximadamente 5,64:1.
Isso descreve esse par de cores, não uma certificação de acessibilidade de toda
a página. A referência consultada foi o critério de
[contraste mínimo da W3C](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

## Referências pesquisadas

Pesquisa em páginas oficiais realizada em 18 de outubro de 2026. As referências
orientam decisões de composição e apresentação; seus textos, métricas,
depoimentos, preços e marcas não foram reproduzidos no Orion.

| Referência | Decisão aplicada |
|---|---|
| [Linear](https://linear.app/) | Mostrar um fluxo de trabalho contínuo e exemplos de produto com contexto. |
| [Attio](https://attio.com/) | Explicar a conexão entre informações e tarefas do negócio. |
| [Raycast](https://www.raycast.com/) | Concentrar a interação em uma demonstração principal. |
| [Cal.com](https://cal.com/) | Apresentar agenda e atendimento por meio de situações concretas. |
| [Framer](https://www.framer.com/) | Dar atenção à tipografia, ao ritmo das seções e ao acabamento dos controles. |
| [Fresha](https://www.fresha.com/for-business) | Usar fotografias de trabalho e entradas por segmento. |
| [SimplyBook.me](https://simplybook.me/en/) | Separar os contextos de uso para tornar as funções compreensíveis. |
| [Square Appointments](https://squareup.com/us/en/appointments) | Conectar agendamento e operação diária. |
| [Intercom](https://www.intercom.com/) | Distinguir conhecer o produto de solicitar uma conversa comercial. |
| [Hostinger](https://www.hostinger.com/br) | Observar a hierarquia de chamadas e a apresentação de planos. |
| [HostGator](https://www.hostgator.com.br/criador-de-sites) | Alternar a expressão em destaque, como no exemplo enviado pelo usuário. |

## Percurso da página

1. Marca, navegação por âncoras, acesso ao painel e chamada para solicitar teste.
2. Título “Um Orion para…” com cinco segmentos, fotografia contextual, seleção
   manual e controle de pausa.
3. Demonstração com quatro abas: Atendimento, Agenda, Clientes e Gestão.
4. Recursos de atendimento, operação e acompanhamento.
5. Entradas para barbearia, estética, odontologia, restaurante e personal trainer.
6. Vídeo breve de uma rotina profissional, iniciado somente pelo visitante.
7. Explicação da solicitação, configuração e ativação assistida.
8. Teste gratuito de três dias e três opções de interesse em assinatura.
9. FAQ com oito perguntas sobre funções, conexão, teste, configuração e planos.
10. Formulário de contato, confirmação de recebimento e informações sobre dados.

A expressão do título alterna entre “sua barbearia.”, “sua clínica.”,
“seu consultório.”, “seu restaurante.” e “seu estúdio.”. Ela ocupa uma área
reservada para evitar deslocamentos de conteúdo. Leitores de tela recebem um
título estável: “Um Orion para o seu negócio.”.

## Promessas e condições comerciais

Essencial, Profissional e Escala são nomes de apresentação provisórios para as
três assinaturas em preparação. O código não inventa preços, franquias, limites,
direitos de uso ou registros em `plans`. Cada chamada apenas identifica o
interesse enviado à equipe.

O teste dura três dias **após a ativação assistida**, conforme combinado com a
equipe. O formulário não abre uma conta, cria empresa, associa membership,
ativa assinatura, inicia cronômetro ou realiza cobrança. A implementação atual
de autenticação não possui provisionamento público de empresa e teste. Por
isso a chamada é “Solicitar teste grátis”.

As barbearias são o ponto de partida. Os demais segmentos são apresentados como
operações que a equipe avalia e configura antes da ativação. Odontologia e
estética usam exemplos administrativos. A landing não promete prontuário,
conduta clínica ou automação médica. Catálogo e pedidos dependem dos módulos
disponíveis e não são apresentados como checkout de pagamentos.

O histórico completo de conversas é restrito a suporte/admin no produto atual.
A landing não oferece esse acesso como uma capacidade geral dos membros de
empresas. A demonstração da conversa ilustra o atendimento no WhatsApp e sua
conexão com a agenda.

## Captação e operação

O formulário registra nome, e-mail, segmento, intenção e consentimento para
responder à solicitação. WhatsApp é opcional. O registro é confirmado apenas
depois de um commit no PostgreSQL e de uma resposta válida da API.

Falhas preservam os campos. A mesma solicitação usa a mesma chave idempotente
em uma nova tentativa, evitando duplicação quando a rede não confirma o
resultado. A chave e os contatos ficam em memória, sem armazenamento local.

As solicitações aparecem em **Configurações → Solicitações do site**, visível
somente a suporte/admin global. Há busca, filtros, paginação, detalhes e estados
Nova, Contatada e Encerrada. Alterar o estado não envia uma mensagem.

Contrato da API, autorização, migração e procedimentos:
[sales-requests-runbook.md](sales-requests-runbook.md).

## Mídia e carregamento

As cinco fotografias são WebP locais. O vídeo tem dez segundos, é H.264 sem
áudio, usa poster local e `preload="none"`. A reprodução requer clique e pausa
quando sai da área visível ou a página fica oculta.

As oito mídias binárias, incluindo a imagem de compartilhamento de 1200 × 630,
somam 1.601.614 bytes. O vídeo responde por 980.074 bytes e não é baixado como
parte obrigatória da abertura da página. CSS e JavaScript não dependem de
bibliotecas externas, rastreadores ou players incorporados.

Autores, URLs, licenças consultadas e hashes estão em
[media-sources.md](../landing/assets/media-sources.md) e
[media-sources.json](../landing/assets/media-sources.json). A imagem social foi
desenhada em SVG, mantendo a marca oficial. Seu arquivo fonte é
[orion-social.svg](design/orion-social.svg).

## Responsividade e acessibilidade

- Grades de duas ou três colunas viram uma coluna em telas menores.
- O menu móvel tem estado expandido, fecha por Escape, link, clique externo e
  mudança para desktop, com devolução de foco quando apropriada.
- Abas usam setas, Home, End, foco único e vínculos ARIA entre controle e painel.
- Campos e selects usam pelo menos 16 px para evitar zoom de foco no iPhone.
- Controles possuem foco visível. O FAQ usa `details` e `summary` nativos.
- A rotação respeita pausa explícita, movimento reduzido, economia de dados,
  visibilidade da página e presença do hero na área observada.
- O formulário valida campos e consentimento, bloqueia repetição durante o
  envio e comunica falha ou confirmação sem apagar dados prematuramente.
- Sem JavaScript, o conteúdo principal continua disponível e o envio fica
  desabilitado com uma explicação. A demo não chama APIs nem envia mensagens.

Os breakpoints principais são 1600, 1199, 959, 639 e 359 px. A revisão estática
também conferiu a largura das expressões do título com a fonte real. Essa
medição não substitui uma renderização de navegador.

## Verificação e limite da prévia

Os testes funcionais da landing ficam em
`ui-tests/orion-landing.test.mjs`; o acompanhamento administrativo é coberto por
`ui-tests/site-requests.test.mjs`. Os testes incluem navegação, estados de
movimento, validação, idempotência, sucesso confirmado e falhas de transporte.

Backend, migração, PostgreSQL, Redis e a matriz de regressão são verificados
antes da publicação do commit. Usar dados sintéticos em bancos descartáveis,
sem enviar contatos fictícios à produção.

A prévia gerenciada do ambiente não iniciou: o sandbox retornou
`bwrap: Can't mount proc on /newroot/proc: Operation not permitted`. A repetição
pelo mesmo supervisor autorizado teve o mesmo resultado. Não foi criado um
servidor paralelo para contornar essa limitação.

**A renderização da nova landing no navegador ainda não foi verificada neste
ambiente.** Testes de DOM não comprovam ausência de cortes, sobreposições ou
problemas visuais em aparelhos reais. Após o deploy manual, conferir ao menos
320/390 px, 768/820 px e 1280/1440 px, além de orientação horizontal, zoom de
texto, menu, demonstração, FAQ, vídeo e formulário.

## Publicação

O código é publicado no repositório existente, com os testes do GitHub no
commit da entrega. O serviço de API recebe o mesmo código e uma etapa de
migração específica para a tabela de solicitações. A verificação de QR do
pre-deploy continua preservada.

As alterações no Railway ficam preparadas para **aplicação manual pelo
usuário**. Nenhuma chamada automática de aceite/deploy integra esta entrega.
