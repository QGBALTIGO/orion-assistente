# Etapa 19 — Link e QR do WhatsApp (candidata)

## Fonte e limites

- Pedido do proprietário: funcionalidade equivalente à ferramenta Link e QR do BotBot, porém com a identidade do Orion, restrita a empresas ativas de serviços de assinaturas. Sem compras de complementos.
- Formato de URL baseado na documentação oficial do WhatsApp Help Center: https://faq.whatsapp.com/5913398998672934
- O conteúdo do formulário autenticado do BotBot não ficou acessível nesta conversa. Esta é uma versão provisória, não uma alegação de paridade completa.

## Implementação proposta

- Interface Orion: número E.164 (+, DDI e número), mensagem opcional, gerar link, copiar, abrir WhatsApp, baixar QR PNG.
- A API exige proprietário e `whatsapp_link.manage` em empresa ativa de serviços de assinaturas; nenhuma empresa de outro nicho recebe esse recurso.
- Usa somente a biblioteca `qrcode[pil]` já fixada nas dependências do Orion. A imagem nasce na própria API e é devolvida como PNG codificado; não há serviço pago ou servidor de QR terceirizado.
- Link `https://wa.me/<número-sem-plus>`, com `?text=` e texto URL-encoded quando presente. O WhatsApp apenas preenche a mensagem: não envia até o próprio usuário decidir.
- Sem conexão Baileys/QR bridge, sem geração de link curto dependente de fornecedor, sem analytics, sem armazenamento de número/mensagem/QR.
- Limites: 360 caracteres de texto, no máximo 1.200 bytes UTF-8 antes de codificar, 1.500 bytes de URL/QR; rejeitar entradas fora de E.164, caracteres perigosos, URLs inválidas e PNGs acima do limite.
- QR contém exatamente o mesmo URL exibido. Contratos de API e UI validam origem HTTPS `wa.me`, esquema PNG e autorização.

## Para concluir

1. Suítes unitárias, imagem PNG válida, normalização Unicode e erros.
2. Suite de DOM do painel em cenário sintético, mobile e desktop, com tema claro e escuro.
3. Testes de autorização para nicho e empresa inativa, CI existente e ausência de efeitos externos.
4. Revisar contraste, espaçamento e foco no navegador e confirmar a tela da referência BotBot se ela ficar acessível.
5. Só depois abrir/publicar a função em produção. Esta branch NÃO depende da PR 274 nem altera a etapa 18.
