# Mensagem com botão de link

No Orion, **Botão com link** é uma mensagem com texto, um botão que abre um endereço HTTPS e rodapé opcional. O tipo de contrato é `url_button`. O exemplo visual solicitado é um botão como “Acessar meu sistema”, abaixo do texto.

O recurso está disponível para escolha explícita em **Enviar Mensagem** e como ação no editor do **Chatbot** de serviços de assinaturas. A implementação não habilita regras, campanhas, agendamentos ou outro nicho, e não altera configurações existentes. A biblioteca de templates, as campanhas e os agendamentos não foram ampliados para esse novo tipo nesta versão.

## Conteúdo e validação

| Campo | Contrato |
| --- | --- |
| `text` | Corpo da mensagem, até 1.024 unidades UTF-16; admite quebras de linha e a formatação textual existente |
| `button_text` | Rótulo do único botão, até 20 unidades UTF-16; sem caracteres de controle C0/DEL |
| `url` | Endereço HTTPS literal, até 2.048 unidades UTF-16; sem credenciais, fragmento, tags de contato ou endereços locais/privados conhecidos |
| `footer` | Rodapé opcional, até 60 unidades UTF-16 |

O envio avulso conserva o conteúdo literal, conforme o contrato existente dessa área. A ação salva do Chatbot utiliza somente as cinco tags de contato já suportadas: `{name}`, `{phone_number}`, `{my_name}`, `{my_email}` e `{my_contact_number}`. O conteúdo é revalidado depois da substituição. O endereço do botão não recebe interpolação de dados de contato.

O servidor não busca a página, não gera prévia remota e não segue redirecionamentos para preparar o botão. A validação do endereço é sintática; não declara que uma resposta futura do DNS seja confiável. Quem abre o endereço é o cliente WhatsApp, após o clique do destinatário.

## Envio e autorização

O envio avulso usa a revisão e a confirmação existentes. Dispositivo, destinatário, texto, rótulo, rodapé e endereço são congelados e entram no fingerprint da revisão. Mudança de conteúdo exige nova revisão. Consentimento, opt-out, exclusão de envio para o próprio dispositivo, propriedade, empresa, geração, revisão, finalidade e responsabilidade pela sessão continuam obrigatórios.

Somente o novo formato exige uma prova atual de capacidade no transporte antes da revisão e da confirmação. A consulta autenticada de estado ocorre fora das transações que bloqueiam a empresa/dispositivo, seguida de revalidação do escopo. Ausência de `capabilities.url_button=true`, resposta de outro dispositivo ou mudança da conexão impede a admissão. Replays idempotentes de operações já concluídas continuam disponíveis mesmo durante indisponibilidade. Os formatos existentes mantêm seu contrato.

Uma versão antiga da bridge ignora a ação desconhecida antes de claim; ela pode manter esse trabalho pendente até expirar. Por isso, um rollback exige considerar pendências do novo formato e não deve convertê-las em texto ou fabricar um novo envio. A verificação de capacidade protege a admissão do envio avulso; não é uma promessa de compatibilidade de código antigo com novos tipos.

A bridge monta `interactiveMessage.nativeFlowMessage` com um único `cta_url`. Somente `display_text` e `url` entram nos parâmetros desse botão. O consumidor não pode fornecer nodes de protocolo, outro botão ou ação arbitrária. O protocolo completo é guardado no cache cifrado antes do relay e utiliza a resolução autenticada do destinatário e as guardas atuais.

O clique abre um site. Ele não é seleção de lista, não cria uma opção de negócio e não aciona uma chamada HTTP no servidor. O simulador e a revisão exibem uma prévia declarativa e escapada, sem visitar o endereço. Histórico e reenvio preservam o endereço e o conteúdo originais; resultado incerto não provoca um segundo envio automático.

O novo formato também usa a presença direcionada de preparação e a revalidação final descritas em [Presença durante a preparação de mensagens](whatsapp-outgoing-presence-20261105.md). Essa qualificação posterior tem seu próprio conjunto de testes; os números abaixo descrevem a etapa inicial do botão.

## Qualificação

Os testes cobrem limites de texto e Unicode, endereços inválidos e privados, conteúdo congelado, isolamento e revogação, cache anterior ao envio, revisão/confirmação, opt-out e a interface. A construção de protocolo e sua aceitação não comprovam a apresentação no destinatário.

| Qualificação local antes da publicação | Resultado |
| --- | --- |
| Python dos 14 módulos afetados | 630 aprovados, zero falhas, erros ou skips |
| Interface dos quatro módulos envolvidos | 190 aprovados, zero falhas ou skips |
| PostgreSQL real para o novo formato | Quatro cenários aprovados: fluxo completo concorrente, opt-out posterior e ausência de capacidade antes da revisão/confirmação. A consulta real de lease tem prazo de três segundos e comprova ausência de auto-bloqueio SQL |
| Transporte Node completo, comparado com `8f9dd64` | 1.098 casos: 1.074 aprovados, as mesmas 19 falhas herdadas do fixture de painel e os mesmos cinco skips POSIX do Windows; 11 casos adicionais aprovados e nenhuma falha nova |

Esses conjuntos se sobrepõem e não devem ser somados. A qualificação Linux do commit e a apresentação real no WhatsApp continuam sendo verificações separadas.

O piloto real deve usar somente o destinatário já autorizado e um endereço público próprio, como `https://orionagenda.pro/`. Deve conferir corpo, botão visível, endereço aberto, recibo e ausência de efeito de negócio. Não é necessário salvar ou ativar uma regra para esse teste.

O SDK continua em Baileys 6.7.24, sem atualização ou dependência nova. O retry interno dessa versão reapresenta o protocolo por `getMessage`, mas não repassa automaticamente todos os hints de `additionalNodes` da primeira tentativa. Não se atribui compatibilidade universal de renderização ou retry sem observação real em cada cliente relevante. Android físico e outros formatos devem permanecer identificados como não homologados quando não executados.

Referências do protocolo: [WAProto da versão fixada](https://github.com/WhiskeySockets/Baileys/blob/v6.7.24/WAProto/WAProto.proto), [relay da versão fixada](https://github.com/WhiskeySockets/Baileys/blob/v6.7.24/src/Socket/messages-send.ts). Essas interfaces permitem compor o payload; a exibição real continua sendo uma verificação separada.
