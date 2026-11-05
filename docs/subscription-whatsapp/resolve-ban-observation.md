# Resolver Banimento — observação e contrato

## Referência gratuita

Em 2026-11-05, após a conclusão ao vivo de Evitar Banimento, foi aberta
<https://botbot.chat/user/whatsapp/resolve-ban>. A página é um guia com
contexto sobre bloqueios, motivos frequentes, **dois passos**, sete links
de referência e o atalho Meus Dispositivos. Não há formulário de recurso,
consulta de número, envio ao suporte, cobrança ou desbloqueio automático.

Os dois passos observados são solicitar revisão no aplicativo e usar o
aplicativo oficial, reconectando o canal após a liberação. O atalho foi
exercitado e chegou a `/user/device`, com a página Dispositivo acessível;
depois o navegador retornou ao guia, sem erros de console. Nenhum recurso,
contato com suporte, SMS, instalação ou reconexão foi iniciado.

## Conferência com as fontes oficiais

As fontes foram consultadas em 2026-11-05, antes da redação do artigo Orion.
Os links da referência foram observados diretamente no DOM. A leitura
web de alguns artigos retornou título sem corpo; nesses casos, os fatos
foram confirmados por conteúdo indexado no domínio oficial ou por outro
artigo oficial que trata do mesmo tema. Não se afirma leitura integral
de todos os sete destinos originais.

| Tema | Fonte oficial utilizada | Evidência aplicável |
|---|---|---|
| Banimento e revisão | [Sobre banimentos](https://faq.whatsapp.com/465883178708358) | Solicitar revisão quando disponível; decisão e status no app; somente o WhatsApp pode remover o banimento |
| WhatsApp Business | [Banimento no Business](https://faq.whatsapp.com/723378546580115/) | Conteúdo oficial indexado confirma o fluxo de revisão no app |
| Bloqueio temporário | [Contas temporariamente banidas](https://faq.whatsapp.com/1848531392146538/) | Conteúdo oficial indexado relaciona uso de apps não oficiais e coleta indevida de informações |
| Após liberação | [Depois do desbloqueio](https://faq.whatsapp.com/1297640017664693/) | App confirma a liberação e solicita nova verificação do número |
| Número usado anteriormente | [Sobre banimentos](https://faq.whatsapp.com/465883178708358) | Artigo inclui a possibilidade de infração pelo antigo titular |
| Aplicativos não oficiais | [Apps não oficiais](https://faq.whatsapp.com/1217634902127718/) | Usar o aplicativo oficial; versões de terceiros não têm suporte ou garantia de transferência do histórico |
| Política de mensagens | [Política do WhatsApp Business](https://whatsappbusiness.com/policy/) | Regras atuais de comunicação empresarial |

O endereço antigo `https://faq.whatsapp.com/857670441897710/`, relacionado
a número herdado, não pôde ser acessado pela ferramenta de leitura. Isso
não comprova um erro 404 universal. O Orion aponta esse tópico para o
artigo atual de banimentos, cujo conteúdo inclui o caso do antigo titular.
O artigo atual de aplicativos não oficiais foi alcançado por link da
própria Central. Os sete tópicos são mantidos; dois apontam para a mesma
fonte que cobre ambos. Nenhum parâmetro de pesquisa ou dado de conta é
incluído nos links do produto.

Também foram consultadas as orientações oficiais de
[comunicação segura com o suporte](https://faq.whatsapp.com/854037192262196/)
e de [golpes e mensagens suspeitas](https://faq.whatsapp.com/2286952358121083/),
que sustentam o cuidado com promessas pagas de desbloqueio.

## Ajustes de precisão

A referência recomenda escrever em inglês para acelerar a análise e
indica 24–48 horas. Não foi encontrada confirmação oficial para a vantagem
do idioma. O artigo oficial atual informa que a maioria das revisões
termina em 24 horas; isso não é garantia de prazo ou de resultado. O guia
Orion orienta acompanhar o que o próprio aplicativo informa.

Também não trata todo aviso como exclusão permanente do número ou de
todos os dados. A existência de revisão depende da opção oferecida pelo
WhatsApp; se ela não aparecer, a Central informa que o banimento é final
e não admite recurso. As verificações de identidade/número pertencem ao
próprio aplicativo; o Orion não solicita códigos de SMS ou credenciais.
Depois da liberação, o atalho leva ao módulo de Dispositivos, cujos
controles existentes permitem acompanhar e refazer a conexão quando
necessário. Abrir o atalho não executa essas operações.

O texto foi reescrito, preservando o guia de dois passos e as referências,
sem promessa de remoção de bloqueio por terceiro ou afirmações não
confirmadas sobre idioma, atendimento telefônico ou prazo garantido.

## Contrato Orion e validação

Rota: `/painel?aba=resolver-banimento`. Reutiliza
`whatsapp_guidance.view`, com proprietário, empresa ativa e workspace de
assinaturas, independentemente de plano comercial. O atalho respeita
`devices.manage`. Usa o artigo de ajuda e os componentes nativos.

Não há novo endpoint, banco, provedor, CSS, dependência, formulário ou
operação de envio. Os links oficiais são âncoras estáticas com abertura
segura em outra aba; não fazem consultas externas automáticas.

O critério de publicação é a validação focada da rota, acesso sem plano,
dois passos, referências, atalho, ausência de mutações e layout nos três
tamanhos e dois temas, seguida de conferência no Chrome real. Os
resultados automáticos gerais permanecem separados e não são tratados
como aprovados antes de concluírem. A etapa 16 começa somente depois de
publicar e conferir esta etapa.

### Verificação focada da candidata

O artigo e a rota passaram nos 22 casos DOM compartilhados dos guias, sem
falhas ou casos ignorados. Os três casos novos cobrem o artigo sem plano
comercial, os sete tópicos oficiais, o atalho de ida e volta e sua ausência
sem `devices.manage`. As fronteiras de empresa ativa, workspace e permissão
também foram aplicadas à nova rota. Sintaxe JavaScript e `git diff --check`
passaram. A revisão do diff confirmou ausência de novo estado operacional,
endpoint ou alteração na ponte.

No Chromium, os nove casos focados passaram em 14,11 segundos, com 45 casos
fora do recorte selecionado. Foram verificados 390×844, 768×1024 e 1440×900
nos temas claro e escuro, além das três fronteiras negativas. O atalho foi
exercitado nos layouts de celular e desktop e retornou ao artigo. Todos os
nove registros apresentaram zero erros de página, requisições externas ou
mutações. As capturas de celular escuro e desktop claro foram inspecionadas
visualmente, sem cortes. Não foi repetida a bateria das etapas 13 e 14.

Esses resultados qualificam o artigo, suas regras de acesso e a navegação;
não comprovam remoção de banimento ou ação do WhatsApp sobre uma conta.
O estado da publicação e a conferência ao vivo serão registrados após
observar a revisão em produção.
