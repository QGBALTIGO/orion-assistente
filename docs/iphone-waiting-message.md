# Diagnóstico controlado — "Aguardando mensagem" no iPhone

O texto **"Aguardando mensagem" no iPhone** indica que o aparelho ainda
não conseguiu exibir/descriptografar a mensagem. No painel Orion, `aguardando`
(pending) e `servidor aceitou` são **estados distintos de confirmação
do transporte**; nenhum desses estados comprova que o iPhone leu o conteúdo.

## Próximo teste, com consentimento do destinatário

1. Preserve o WhatsApp vinculado e **não apague /data/auth**.
2. No painel, identifique o contato afetado, verifique o número em
   **Verificar destinatário sem enviar mensagem**.
3. Somente com consentimento e em um horário combinado, escreva **uma nova**
   mensagem curta, marque **Teste pontual: consultar novamente os dispositivos**
   e confirme manualmente o envio. Não reenvie os textos antigos.
4. Confira **no iPhone** se o texto ficou legível imediatamente e compare com
   um contato que já recebe na hora. Registre horário, estado final e
   contagem de pedidos de recuperação sem compartilhar números, chaves ou
   conteúdo das mensagens.

O modo manual define `useUserDevicesCache:false` em **um** envio pelo
`relayMessage` do Baileys. Isso força a consulta da lista de dispositivos
em vez de usar o cache de dispositivo, mas **não cria uma nova sessão Signal**,
não limpa chaves, não resolve necessariamente conflitos PN/LID e **não garante**
que o iPhone consiga descriptografar.

Se `QR_RELIABLE_SEND_V2` não estiver habilitado, o teste falha de forma segura,
**sem enviar mensagem**. O valor não deve ser alterado sem revisão operacional.
O modo é desligado de novo no formulário após o envio e não persiste por contato.

## Limites e próximos passos

- Não repita manualmente mensagens com confirmação ambígua: o destinatário
  poderá recebê-las mais tarde e duplicá-las.
- A API privada exige autenticação de suporte e mantém a taxa máxima de envio.
- Em caso de falha persistente, comparar destinatário PN/LID, pedidos de retry,
  mudanças de device-list e logs **sanitizados**, e avaliar Baileys 7 em
  ambiente separado com sessão de teste. Nunca migrar a sessão de produção às cegas.
- Não habilitar campanhas ou respostas automáticas como método de diagnóstico.
- A WhatsApp Cloud API oficial permanece a alternativa de arquitetura para
  uso comercial, sujeita a número autorizado, políticas e custos da Meta.

**Critério de sucesso:** mensagem nova legível imediatamente no iPhone afetado,
mais recibo de entrega quando disponibilizado. Testes automatizados cobrem
somente validação, segurança e roteamento da opção; não simulam criptografia
real entre dispositivos.
