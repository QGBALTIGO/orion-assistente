# Canário nativo no WhatsApp principal da Orion

O usuário informou indisponibilidade temporária do Baltigo e autorizou continuar a bateria de testes somente pela Orion. A conexão principal utiliza o transporte central existente; ela não deve ser duplicada, transformada em dispositivo gerenciado ou receber cópias de credenciais para executar esses ensaios.

O canário é uma extensão isolada de teste. Não habilita regras, nichos, campanhas, grupos ou agendamentos, nem torna o transporte principal uma API genérica de protocolos fornecidos pelo cliente. O novo botão continua disponível para escolha explícita nas áreas gerenciadas descritas no contrato próprio.

## Comandos restritos

| Comando exato | Conteúdo de teste |
| --- | --- |
| `ORIONTESTE LISTA` | Lista nativa de dois serviços fictícios de barbearia. A escolha pode produzir uma única confirmação fictícia, sem reservar horário ou contratar serviço |
| `ORIONTESTE BOTAO` | Mensagem com um botão para abrir somente `https://orionagenda.pro/`; o clique não executa ação de negócio |
| `ORIONTESTE AUDIO` | Arquivo fixo de sons curtos, sem fala ou dados de clientes, para conferir preparação, gravação e reprodução |

O comando antigo `ORIONTESTE` conserva seu comportamento. Os novos comandos exigem `QR_NATIVE_TEST_ENABLED=1`, um único telefone definido explicitamente em `QR_NATIVE_TEST_PHONE`, remetente privado comprovado e autorização atual do transporte central. O padrão é desabilitado. Não são aceitos URLs, caminhos, arquivos, destinatários ou parâmetros livres nos comandos.

O canário recusa admissão enquanto houver atendimento em curso ou pendência de ingresso que impeça automação, sem cancelar a IA ou alterar suas filas e permissões. Essa restrição afeta somente o teste. Uma entrada nova do participante invalida a preparação antiga do teste; um LID ainda não comprovado pode cancelar conservadoramente o canário, sem cancelar o atendimento de outro contato. A resolução do remetente tem limite de três segundos.

## Persistência, seleção e envio

A reserva cifrada e durável antecede o envio, com limite de 64 operações e recusa segura quando não há armazenamento ou capacidade. Uma operação com resultado incerto não é repetida com nova identificação. O observador conserva a revisão e o horário originais de até 256 entradas, sem evictar entradas válidas para admitir outras; seu limite recusa somente o canário. Comandos anteriores ao início da conexão atual são recusados, com tolerância de um segundo para a precisão do timestamp. Replays, mensagens próprias, entradas antigas e mensagens de sincronização não criam novos canários.

Uma escolha exige o token emitido no menu, a identificação exata da mensagem citada, o mesmo destinatário e o contexto vigente da sessão, empresa, canal, responsável e concessão. Entrada e menu têm prazo original de cinco minutos, novamente conferido depois da espera/upload e antes do cache e relay; o menor prazo prevalece na seleção. Uma duplicata não renova a validade. A escolha é consumida de forma durável antes da única resposta fictícia. Tokens inválidos, expirados ou repetidos desse namespace não são encaminhados à IA.

Os envios usam o mesmo socket, resolução autenticada do destinatário, cache cifrado anterior ao relay, guardas de autoridade e coordenador de presença. A espera proporcional não concede autorização: o contexto é verificado novamente depois das operações assíncronas e antes do envio. A observação de um relay já aceito é registrada sincronamente, sem uma nova espera de autorização que possa descartar um clique rápido; isso não permite outro envio. Não são alteradas chaves ou algoritmos de criptografia.

## Arquivo de áudio fixo

O arquivo `qr-bridge/src/fixtures/native-test-tone-2s.mp3` foi gerado localmente a partir de tons baixos de 440 Hz intercalados com silêncio. LAME e Mutagen foram ferramentas locais de preparação; não são dependências da aplicação.

| Propriedade | Valor |
| --- | --- |
| Tipo | MP3, mono, 16.000 Hz, 64 kbit/s |
| Tamanho | 16.704 bytes |
| Duração pelo parser de cabeçalho | 2,088 segundos; o padding do codec está incluído |
| SHA-256 | `04a38cec1b5110832117985ae2f61309ff9818db4f3350407fa206afedf23f17` |

O loader aceita somente o arquivo interno e a identificação sintética fixa. Hash, tamanho, tipo e duração fazem parte da validação. O CI exige decode real com FFmpeg e confirmação por ffprobe; o parser local sozinho não comprova reprodução. Uma falha de arquivo não permite fallback para caminhos ou mídia fornecidos pelo cliente.

## Qualificação

Código, testes automatizados, publicação, recibos e apresentação no WhatsApp são etapas distintas. Esta especificação não declara que os três canários já foram publicados ou qualificados em telefones físicos. A homologação deve registrar comando, apresentação, seleção ou endereço aberto, reprodução e indicação de preparação observadas. Android e iPhone precisam ser identificados separadamente quando houver ou faltar teste físico.

Na versão local congelada, os seis módulos focados passaram com 117 casos, sem falhas ou skips. A revisão independente conferiu os oito hashes de fontes/testes/fixture e executou os dois módulos novos: 39 casos aprovados, sem falhas, skips ou cancelamentos. São conjuntos sobrepostos.

O Node completo, comparado à base imutável `fc13f5e`, executou 1.245 casos: 1.221 aprovados, as mesmas 19 falhas herdadas do fixture de painel, os mesmos cinco skips POSIX no Windows e nenhum cancelamento. Não surgiram identidades novas de falha; os 96 casos adicionais em relação à base passaram. Isso não substitui CI Linux, decode real do arquivo ou ensaio no destinatário.
