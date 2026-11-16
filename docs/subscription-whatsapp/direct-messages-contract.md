# Etapa 11 — contrato de Enviar Mensagem no Orion

Definido em 05/11/2026 após a [observação da referência](direct-messages-observation.md).
Este documento descreve decisões do Orion, inclusive onde o comportamento
interno da referência não pôde ser observado.

## Acesso e apresentação

- Somente o proprietário de empresa ativa no nicho `servico_assinaturas`,
  workspace `subscription_services`, feature `direct_messages.manage`.
- Rota `/painel?aba=enviar-mensagem`, módulo `OrionSubscriptionDirectMessages`
  e API `/api/v1/tenants/{tenant_id}/subscription-whatsapp/direct-messages`.
- Componentes, cores, tipografia, campos, diálogos, temas e responsividade
  do Orion. Formulário com as abas Texto, Mídia e Template; revisão e resultado
  por destinatário. O histórico consolidado continua em Mensagens Enviadas.
- Rascunhos separados por aba, mantidos somente em memória durante a visita.
  Alternar abas preserva seus campos; somente a modalidade ativa compõe o
  pedido. Recarregar, sair ou trocar de contexto limpa os conteúdos privados.
- Sem novo plano, provedor ou complemento pago. A Central legada permanece
  separada do workspace de assinaturas.

## Destinatários e conteúdo

Texto e Template recebem um número manual com código do país **ou** de um
a dez contatos do catálogo, escolhidos individualmente. Não combinam as duas
fontes no mesmo pedido; não há selecionar todos, grupos ou lista manual.
As chaves de contatos são únicas e resolvidas no servidor. O número aceita
pontuação comum e é normalizado para 8–15 dígitos internacionais. Mídia
recebe somente um número manual e um arquivo.

Texto simples é literal, até 1.000 unidades UTF-16, sem expansão de tags.
Espaços nas bordas e quebras de linha são preservados no envio avulso.
A edição oferece negrito, itálico, monoespaço, riscado e emoji, inseridos
na seleção ou posição do cursor; não aceita HTML ativo.

Mídia usa a preparação de arquivos já existente em Templates: um upload de
até 10.000.000 bytes, extensões observadas e inspeção real do conteúdo. O
upload cria apenas um asset privado; não cria template ou envio. Imagens,
vídeos e documentos podem levar legenda de até 1.000 unidades UTF-16, com
quebras de linha normalizadas para espaços. Áudio não leva legenda nem
mensagem de texto complementar. A legenda é opcional no Orion; a validação
da referência após selecionar um arquivo não foi observada.

A aba Template usa a biblioteca existente e seus oito formatos. A seleção
de template é congelada na revisão, com suas regras e sequências existentes;
não se altera a literalidade da aba Texto.

Salvar como template é opcional em Texto e Mídia. Quando solicitado, o
nome deve ter 1–100 unidades UTF-16. Exigir esse nome é uma decisão explícita
do Orion; o HTML observado não o exigia. Sem essa opção, nenhum template
oculto ou visível é criado. Com a opção, o template e o envio são persistidos
na mesma confirmação e transação, uma única vez, com resultado recuperável.
O template salvo segue a normalização e as cinco tags já suportadas pela
biblioteca quando for utilizado posteriormente; tags desconhecidas impedem
salvar, com erro de validação. Isso não muda o texto literal do envio atual.
Salvar áudio não transforma a legenda omitida em texto complementar.

## Contrato HTTP

O pedido de revisão contém `idempotency_key` e `message`. A mensagem é uma
união discriminada por `kind`: `text`, `media` ou `template`. Todas recebem
`device_id` e `recipients`:

```json
{"mode":"phone","phone":"5567999999901","contact_keys":[]}
```

Ou, somente para Texto/Template:

```json
{"mode":"contacts","contact_keys":["phone:5567999999901"]}
```

Texto acrescenta `text`; Mídia, `asset_id`, `file_name` e `caption`; Template,
`template_id`. Somente Texto/Mídia aceitam `save_template: {"name":"Nome"}`.
Campos de outra modalidade são rejeitados, não ignorados silenciosamente.

- `GET /options`: dispositivos, templates, revisão, capacidades e limites.
- `GET /contacts`: pesquisa e paginação do catálogo autorizado.
- `POST /review`: revisão persistida válida por 120 segundos.
- `POST /confirm`: mesma chave, hash da revisão, `ENVIAR` e consentimento.
- `GET /requests/{key}`: recuperação por conta e empresa, sem novo envio.
- `GET /{send_id}`: estado e destinatários da operação confirmada.
- `POST /{send_id}/actions`: cancelar somente trabalho ainda pendente, com
  versão atual, chave idempotente e confirmação `CANCELAR`.

A revisão mostra destinatários, bloqueios, partes efetivas, avisos e o
template a criar, quando houver. A operação retorna `send_id`; o resultado
contém `send` e `saved_template`. A URL pode usar `message_id` para abrir
o acompanhamento. Respostas incertas conservam a identidade da operação;
uma consulta negativa não autoriza trocar a chave e repetir a confirmação.

## Execução e isolamento

A execução reutiliza a fila, efeitos `bulk_action`, recibos e transporte
validado da etapa 09. A origem persistida `direct` é diferente de `bulk`
e também da origem de audiência `manual`/`catalog`. As APIs, pedidos e totais
de Massa não incorporam envios diretos; Mensagens Enviadas mostra sua origem
como Envio direto. Nenhum reenvio genérico é liberado para um efeito
administrado por essa fila.

Dispositivo, conteúdo, asset, fontes e permissões são congelados na revisão
e revalidados na confirmação e antes do transporte. Opt-out, próprio número
do dispositivo, mudança de empresa, revogação e dispositivo indisponível
continuam bloqueando a execução. A permissão efetiva depende da origem
persistida, inclusive na autorização de arquivos privados.

A cadência compartilhada com Massa, Programadas e Campanhas permanece ativa.
Não há garantia de início instantâneo, entrega ou leitura. Resultados
incertos não geram repetição automática; cancelar não desfaz mensagem aceita.
Fechar a página não cancela trabalho confirmado no servidor.

## Persistência e qualificação

Migração 0071 sobre 0070, com origem explícita em lotes e pedidos, restrições
de integridade, escopo de empresa e RLS preservados. A publicação exige backup
privado verificado antes da DDL e barreiras de catálogo de API e worker.
Não ampliar o papel do cliente nem os grants dos outros nichos.

Testar os três modos, validação de contatos/limites, mídia real e áudio sem
legenda, criação atômica e idempotente de template, origem/isolamento,
concorrência, consentimento, revogação, cadência, recuperação, recibos,
cancelamento e integração de interface. Chrome e WebKit cobrem os tamanhos
e temas existentes. A conferência em produção é de leitura, sem mensagens
reais de teste. A etapa 12 começa após publicar e conferir esta entrega.
