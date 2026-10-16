# Interface QR e conversas — homologação

- `/panel`: login `admin` + senha administradora fornecida de modo
  seguro pelo operador de implantação. Nenhum SUPPORT_API_TOKEN é exibido
  para o usuário. Cookie HttpOnly, Secure, SameSite Strict; sessão opaca
  aleatória de 12h no Redis; CSRF para mutações; limite de tentativas.
- Menu **Conectar dispositivo** dentro do painel: guias QR Code/Código,
  instruções passo a passo, QR com atualização periódica, código temporário
  de oito caracteres (DDI + DDD; copiar para o celular), status real.
- Menu **Conversas**: lê apenas mensagens de texto individuais **novas**
  recebidas após pareamento; mostra conversas na RAM; envia mensagens
  **somente ao pressionar Enviar e confirmar o número**, até 5/minuto,
  máx. 1200 caracteres, com uma sessão Baileys vinculada.
- Não coleta mídias ou grupos, nem sincroniza histórico antigo. Mensagens
  em memória somem na reinicialização. Credenciais WhatsApp ficam no
  volume Railway privado, acessível apenas pelo bridge. Nenhum webhook
  Meta e nenhum envio em massa ou chatbot automático são disparados aqui.
- Serviço bridge **sem domínio público**, acessado só pela API privada
  Railway com segredo interno. Sessões do painel usam cookie e Redis;
  o usuário nunca recebe o segredo do bridge.
- Baileys é cliente de dispositivo vinculado **não oficial**. Pode causar
  desconexões/restrições na conta. Teste somente número sob sua gestão.
- Critério de aceitação real: usuário escaneia QR ou digita código,
  o status muda para Conectado; envia texto de um número diferente;
  a caixa Conversas mostra a mensagem; o usuário confirma um envio
  de resposta, que chega ao destinatário. Nenhuma dessas três ações
  externas pode ser simulada como teste real.
