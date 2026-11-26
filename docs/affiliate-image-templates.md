# Identidade visual dos templates de Afiliados

O motor de publicação reside no serviço separado orion-afiliados-piloto
na VPS. O painel na Railway usa seu proxy autenticado. Não publicar uma
alteração do motor apenas na imagem da Railway e alegar que o worker mudou.

- O editor abre o modelo padrão efetivamente utilizado nas novas ofertas,
  permitindo editar marca e layout existentes mesmo em motor legado.
- O motor novo anuncia image_templates_v1. Com essa capacidade o editor
  habilita nome, logo local reduzido (PNG/JPEG <=12 KiB), @perfil,
  estilos, quatro cores, CTA, rodapé e visibilidade de marca, loja,
  desconto, preço anterior e cupom. A indicação de publicidade continua
  obrigatória. A prévia de demonstração usa o próprio renderizador de
  publicação; não cria ofertas, jobs nem envios.
- A legenda permanece em aba separada do mesmo modelo, preservando IDs,
  regras, links, preço e demais dados. Novos modelos e duplicação não
  alteram os existentes. Definir padrão afeta novas ofertas e regras que
  usam o padrão; regras já configuradas continuam com o ID escolhido.
- O antigo seed "PromoTech com IA" / "PromoTech" é migrado somente quando
  pertence a uma empresa Orion que não se chama PromoTech e não possui
  configuração visual moderna. Modelos explicitamente personalizados
  são preservados; marcas vazias usam o nome da empresa. O motor não
  reescreve snapshots de publicações já preparadas ou aprovadas.
- A prévia e o salvamento usam o escopo de proprietário já autenticado.
  Não fazem download de logo por URL nem solicitam acesso a outros hosts.

## Publicação
Validar os testes da branch e a imagem incremental do painel. Esta imagem
preserva a release de senha administrativa e demais alterações publicadas.
O deploy do motor precisa de acesso autorizado à VPS. Conferir o commit
ativo e aplicar as mudanças sobre ele, preservar SQLite e sessão WhatsApp,
fazer backup e usar o fluxo existente affiliate_pilot/deploy/update.py
--app-only somente depois de conferir a base e as restrições do script.
Não reiniciar o conector de WhatsApp nem enviar mensagens durante QA.
Após publicar o motor, confirmar a flag por sessão autenticada, gerar a
prévia, salvar e reabrir o template; validar uma imagem de demonstração.
O painel legado não habilita configurações que ainda não são suportadas.
