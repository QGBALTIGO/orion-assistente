# Aquecimento — observação e contrato

## Referência e acesso

Em 2026-11-05, o Chrome conectado permitiu ler integralmente
<https://botbot.chat/user/whatsapp/warmup>. A página é um guia gratuito de
uso manual, sem formulário, início de execução, agendamento ou cobrança.
Não foi observado um serviço de aquecimento automático.

O atalho **Meus Dispositivos** foi aberto e chegou a
`https://botbot.chat/user/device`, com a página Dispositivo disponível.
Depois da leitura, o navegador voltou ao guia. Nenhum dispositivo, contato,
mensagem ou configuração foi criado ou modificado nesse teste.

## Comportamentos observados

| Parte | Referência observada | Adaptação Orion |
|---|---|---|
| Preparação | WhatsApp Business, verificação do número e preenchimento do perfil | Orientar a conferência de perfil verdadeiro, identificação e controle do número |
| Uso inicial | Interações manuais nos dias 1–2, contatos conhecidos, grupos legítimos, status e chamadas | Mesmo roteiro de preparação manual, com autorização e preferências do contato |
| Progressão | Dias 2–5, formatos texto/imagem/áudio/documento/vídeo e intervalos | Planejar formatos conforme a conversa e reservar tempo para responder |
| Exemplo numérico | Dia 1: 10 contatos; dia 2: 20; dia 3: 50; dia 4: 80; dia 5: 100 | Tabela com os mesmos números e unidade **Contatos**, qualificada como exemplo |
| Conexão | Atalho Meus Dispositivos depois da preparação | Botão interno para a lista Dispositivos, condicionado à autorização existente |
| Erros e sinais | Listas compradas, disparos iniciais, atividade agressiva, falta de resposta, demora e avisos | Orientações para revisar consentimento, conexão, capacidade de resposta e avisos oficiais |
| Prazos | Sugestões de 7 ou 14 dias | Exemplos de organização; sem prazo seguro ou proteção garantida |

O texto foi reescrito para o Orion. Foram mantidos a finalidade, a sequência
manual, a tabela e o atalho, usando o artigo de ajuda, os botões, a tabela,
os ícones e as cores já existentes no painel. Não há CSS, fonte, imagem,
dependência, endpoint, migração, agendador ou operação de provedor novos.

## Precisão das orientações

As fontes oficiais foram consultadas em 2026-11-05:

- [Política de mensagens do WhatsApp Business](https://whatsappbusiness.com/policy/): perfil verdadeiro e atualizado, autorização para contato e respeito à interrupção das mensagens.
- [Diretrizes de mensagens do WhatsApp](https://www.whatsapp.com/legal/messaging-guidelines): mensagens indesejadas, spam, avisos e restrições.

Essas fontes não confirmam que preencher o perfil gera confiança de um
algoritmo, que palavras isoladas como “pix” causam bloqueio, nem que uma
sequência diária ou um prazo de aquecimento garanta aprovação ou entrega.
Essas afirmações não foram reproduzidas como fatos no produto. Os números
observados são identificados como exemplos, em uma nota única. A janela de
atendimento da WhatsApp Business Platform não foi aplicada ao uso via QR.

Os links oficiais são âncoras estáticas com `noopener noreferrer`; o guia
não faz consultas automáticas a esses sites. O comportamento da referência
é informativo, e assim permanece no Orion. Não gera conversas artificiais
nem contorna restrições da plataforma.

## Autorização e isolamento

Rota: `/painel?aba=aquecimento`. Feature: `whatsapp_guidance.view`.
Disponível apenas ao proprietário de empresa ativa no workspace
`subscription_services`, derivado do nicho `servico_assinaturas`.
Planos comerciais de outros nichos não concedem esse acesso. Empresa
inativa, proprietário ausente, contexto não resolvido ou permissão revogada
não exibem o guia. O atalho Dispositivos respeita `devices.manage`.

Este módulo começou depois do registro explícito da pendência da carteira
e das recargas da etapa 12, sob a autorização de continuar pelas funções
acessíveis sem pagar. A etapa 14 só começa após publicação e conferência
ao vivo desta etapa.
