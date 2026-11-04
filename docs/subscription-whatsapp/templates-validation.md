# Etapa 04 — Qualificação de Templates

Base publicada: `8146d5f7f50bb778d188f74b7e52452394870c50` (PR 250).
Observação e decisões: [templates-observation.md](templates-observation.md).

## Escopo

A aba `templates` e a API `/subscription-whatsapp/templates` exigem
`templates.manage`, proprietário e empresa ativa de Serviço de assinaturas.
Biblioteca, Chatbot e simulador usam as mesmas identidades e revisões. A migração
aditiva `0064_subscription_templates` acrescenta os campos da biblioteca e os
tipos áudio/localização, sem criar conteúdo, regras, sessões ou mensagens.

O predeploy exige a revisão anterior, contrato de permissões e backup privado
verificado antes do DDL. O worker confere a estrutura e os papéis do banco antes
de iniciar. As allowlists anteriores reconhecem explicitamente 0064; não foi
adotado um teste amplo que aceite revisões desconhecidas.

## Evidência local

- O gate Python completo passou com **1.085 testes em 57,74 segundos**, incluindo
  API, regras, mídia, arquivos, permissões e gates de publicação. Uma asserção
  adicional do broker privado de áudio foi acrescentada depois dessa rodada e
  será incluída no CI completo.
- O gate da ponte passou com **309 testes**, usando a biblioteca de protocolo
  publicada e fornecedores/sockets sintéticos. Inclui bytes criptografados dos
  novos formatos e a sequência áudio → legenda → mensagem posterior → localização.
- O gate DOM passou com **225 testes**, incluindo 22 da biblioteca e 30 do
  simulador. Os cenários incluem conflitos, repetição idempotente, acesso revogado,
  troca de empresa, respostas atrasadas, arquivos privados e isolamento do nicho.
- **38 casos PostgreSQL coletados**: CRUD, quota, idempotência, concorrência,
  autorização real, formatos preparados por codecs, leitura privada após grant,
  sucessores de efeitos, exportação/importação e barreira de catálogo 0064.
  Coleta local não equivale a execução no banco; esse gate roda no CI.
- **8 cenários Chromium passaram em 46,78 segundos**, com os oito cenários
  WebKit reservados ao CI. Incluem larguras de 390, 768 e 1440 px nos dois temas,
  biblioteca → Chatbot → simulador, decodificação de imagens/áudio/vídeo e
  download explícito de documento. As capturas foram inspecionadas sem corte
  horizontal. São navegadores reais com dimensões emuladas, não aparelhos físicos.
- Revisão independente da integração visual e do simulador sem bloqueador.
  As prévias usam somente Blob local. Documentos não são embutidos; localização
  não busca mapas externos. Uma sondagem real no Chromium confirmou que retirar
  um player da página e revogar seu Blob interrompe sua reprodução.
- A revisão de arquivos identificou e fechou a conversão silenciosa de OGG
  com vídeo em áudio. O teste usa um arquivo real com Theora/Vorbis; capas de
  álbum MP3 identificadas como `attached_pic` continuam aceitas. O conjunto de
  transporte passou com **30 testes em 2,89 segundos** após essa correção.

### Retomada dos validadores PDF, APK e áudio

A retomada encontrou a importação de `subscription_template_pdf` sem o módulo
correspondente no checkpoint. O módulo foi concluído com `pypdf==6.21.4` em modo
estrito, verificando cabeçalho/final, catálogo, páginas e referências dos objetos.
O processo de inspeção aceita até **10.000.000 bytes**, **20.000 objetos**,
**2.000 páginas**, profundidade de **64** e **100.000 entradas percorridas**;
streams decodificados pelo parser têm limite de **20.000.000 bytes**. No Linux,
o processo tem limites de **384 MiB de espaço de endereçamento**, **5 segundos
de CPU** e **10 segundos de tempo total**. Os bytes do documento não são
reescritos. PDFs protegidos por senha são recusados porque seu conteúdo não
pode ser inspecionado por esse fluxo.

As regressões usam PDFs completos, com conteúdo, metadados, objetos nulos e
atualização incremental, além de casos truncados, referências inválidas e
stream incompleto. O APK valida o manifesto XML textual ou binário inteiro
com até **2.000.000 bytes** e profundidade **64**, sem instalar nem executar o
pacote. Foi reproduzido e corrigido também um ZIP/APK com DEFLATE corrompido:
o erro de descompressão agora retorna a resposta de validação **415**, em vez
de escapar como falha interna.

A rodada final focada passou com **175 testes em 9,38 segundos**:
`test_subscription_template_pdf.py`, `test_subscription_template_apk.py`,
`test_subscription_template_transport.py` e `test_subscription_chatbot_media.py`.
Inclui o OGG real com trilha de vídeo recusado e o MP3 com capa aceito. Essa
rodada complementa os gates do módulo; não representa envio a destinatários
reais, análise de malware ou validação de assinaturas PDF/APK.

## Gates para publicar

### Conferência da retomada

A árvore da base local foi comparada com a produção da PR 250 e coincide com
`f911128ea29637f7a1f3af326e01f764f597158e`. A branch efetivamente publicada é
`security/prelaunch-hardening-20261030`; `main` não é a origem desses serviços.

Na retomada, o gate completo de contratos passou com **1.176 testes em 61,75
segundos**. O ajuste posterior de ZIP/PDF foi coberto pela rodada focada de
175 testes acima e será executado novamente no CI. Ponte e DOM passaram com
**309** e **225** testes. A revisão da interface corrigiu o bloqueio visual
após conflito de revisão e a atualização da URL privada de um arquivo
preservado depois da reconciliação explícita; os **23 testes da biblioteca**
passaram após esses ajustes. Nenhuma dessas operações enviou mensagens reais.

O PostgreSQL e o WebKit continuam dependentes do CI. Os resultados locais
não substituem esses gates nem comprovam uma publicação.

O workflow `subscription-chatbot.yml` inclui a nova biblioteca e mantém os
contratos das etapas 01–03: Python, ponte Node, DOM, PostgreSQL 16/Redis 7 e
Chromium/WebKit em celular, tablet e desktop, nos temas claro e escuro.
Fixtures de mídia são sintéticas e decodificáveis. A rede dos cenários é
limitada ao servidor de teste; não há envio para clientes ou consumo de provedor.

Os resultados do CI devem corresponder à árvore enviada para merge. A publicação
exige deploys saudáveis de API, worker e ponte, seguidos de conferência no
Chrome conectado do usuário. A entrega não afirma ter testado recebimento em
um aparelho físico nem comportamentos da referência que ficaram indisponíveis.

Os resultados finais de CI, publicação e conferência ao vivo serão acrescentados
após a execução; esta seção registra o estado do checkpoint anterior à publicação.
