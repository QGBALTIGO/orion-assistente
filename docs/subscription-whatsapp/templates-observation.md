# Etapa 04 — Templates: observação e contrato

Fonte: `https://botbot.chat/user/template?channel=whatsapp`.
Observação em 2026-11-04 pelo Chrome conectado autorizado. Apenas navegação,
pesquisa e inspeção dos formulários; nenhum template salvo ou envio realizado.

## Comportamentos observados

| Estado/ação | Resultado observado |
|---|---|
| Abrir Templates | Título Todos os Modelos, Criar Template, contador 0 / 100, pesquisa e “Nenhum template ainda”. |
| Pesquisar texto sintético | Pesquisa preservada e “Nenhum modelo corresponde à sua pesquisa.” |
| Criar Template | Cinco abas: Texto, Mídia, Figurinha, Recado de Vídeo e Localização. Cada aba tem seu próprio formulário e Salvar. |
| Texto | Nome sem required/maxlength no HTML; mensagem obrigatória, textarea com maxlength 1000. |
| Tags | Modal informativo com `{name}`, `{phone_number}`, `{my_name}`, `{my_email}`, `{my_contact_number}`. Não há inserção automática visível. |
| Mídia | Nome e arquivo obrigatórios. Tipos anunciados: jpg, jpeg, png, webp, pdf, docx, xlsx, csv, txt, mp3, mp4, ogg, wav, opus, apk. Limite anunciado de 10 MB. |
| Legenda e mensagem de mídia | Campos independentes e opcionais, ambos com maxlength 1000. O texto de ajuda especifica que a mensagem é enviada depois do arquivo. |
| Figurinha | Nome e arquivo obrigatórios; jpg, jpeg e png, até 200 KB. Aviso “Esta funcionalidade está atualmente indisponível.” |
| Recado de Vídeo | Nome e arquivo obrigatórios; MP4 até 10 MB e 60 segundos. Mesmo aviso de indisponibilidade. |
| Localização | Nome, latitude e longitude obrigatórios. Coordenadas numéricas com step any; sem min/max no HTML. |

O `accept` do campo Mídia inclui categorias amplas de imagem, áudio e vídeo,
PDF, Word/Excel e APK; o contrato de tipos visível ao usuário é a lista acima.
Os formulários de Figurinha, Recado e Localização reutilizam o nome acessível
“Mídia” na referência, embora a aba selecionada identifique o tipo correto.
Esse defeito de rótulo não será reproduzido.

## Limites da evidência

A biblioteca da conta observada está vazia. Assim, não foram observados cards
de um template salvo, edição, exclusão, dependências, paginação, resposta de
sucesso ou validações do servidor. Não salvamos dados na conta de referência
para contornar essa limitação. Também não houve upload ou envio pelo WhatsApp.
Os fluxos correspondentes do Orion devem ser descritos como implementação e
validação próprias, sem alegar observação integral da referência.

## Adaptação ao Orion

- Manter componentes, tipografia, cores, formulários, avisos e navegação Orion.
- Biblioteca exclusiva de Serviço de assinaturas, com até 100 templates ativos,
  pesquisa, estados vazios e formulários dos cinco tipos observados.
- Reutilizar a biblioteca e referências do Chatbot, preservando identidades e
  revisões. Legenda e mensagem posterior são campos e ações distintos.
- Usar conteúdo de mídia privado, com limites e validação do conteúdo no servidor;
  nenhuma URL externa de arquivo ou execução de documento é necessária.
- Coordenadas têm faixas geográficas válidas. A ausência de min/max no HTML
  da referência não justifica persistir latitude/longitude impossíveis.
- Uma edição deve revalidar as regras dependentes e invalidar planos antigos;
  a exclusão não pode deixar uma regra aparentemente válida com referência quebrada.
- Testes com fixtures sintéticas cobrem armazenamento, integração com Chatbot,
  simulação e plano de transporte, sem disparos para clientes reais.

### Regras operacionais explícitas

O formulário permite mensagem de mídia e legenda separadas. O transporte do
WhatsApp usado no Orion não tem legenda nativa de áudio: nesse caso a legenda
é uma mensagem de texto depois do áudio, seguida pela mensagem posterior quando
preenchida. Cada ação aguarda a confirmação da anterior. A interface explica
esse comportamento quando um áudio é selecionado; não descarta nenhum campo.

O Orion prepara imagens, figurinhas, vídeos e áudios com os codecs reais.
Documentos PDF, DOCX, XLSX e APK são anexos privados preservados, com inspeção
de formato, estrutura e limites; isso não representa execução, renderização,
remoção de metadados nem análise de malware. TXT/CSV exigem UTF-8 íntegro.
Os nomes de arquivo são próprios de cada template e não alteram o asset
deduplicado. CSV preserva a extensão `.csv` e é transportado como texto UTF-8.

O upload da biblioteca aceita até 10.000.000 bytes, ou 200.000 para figurinha.
Os formatos normalizados preservam os limites de transporte já publicados:
imagem JPEG até 5 MB, figurinha WebP estática até 100 KB, vídeo/recado MP4 até
16 MB e documentos até 10 MB. Áudio é preparado em MP3, até 16 MB e 30 minutos,
sem cortar conteúdo para caber. O recado permanece limitado a 60 segundos.
A reserva de mídia privada existente continua em 500 assets e 256 MiB por
empresa, incluindo reservas necessárias a referências e backups; esse limite
técnico não é uma oferta comercial copiada da referência.

Uma edição conserva o tipo do template, permite trocar arquivo do mesmo tipo
e revalida o grafo. A exclusão é lógica e bloqueada para templates usados por
qualquer regra ativa ou inativa. A revisão de todo o Chatbot muda junto com
a edição, para invalidar decisões antigas sem modificar as políticas de
chamadas, transcrição ou webhook do dispositivo. Repetições da mesma solicitação
retornam seu resultado canônico, sem duplicar templates.

Os templates legados importados antes desta etapa continuam legíveis acima dos
novos limites de quantidade/texto; não são apagados nem truncados. Novas
gravações da biblioteca usam 1.000 unidades UTF-16 por campo. A exportação de
respostas continua levando apenas os templates necessários às regras escolhidas;
ela não é apresentada como exportação de toda a biblioteca independente.
