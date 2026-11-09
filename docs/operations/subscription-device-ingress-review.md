# Recuperação segura de mensagens retidas — dispositivos de assinaturas

## Situação de produção (BaltigoFlix, 08/11/2026)
O painel registrou **4 mensagens bloqueadas** e pausou respostas automáticas.
O bloqueio vem do diário criptografado de admissão do QR Bridge. Não indica
necessariamente perda de autenticação do WhatsApp: reiniciar ou refazer vínculo
não resolve o diário e pode prejudicar a recuperação. O diagnóstico de
transporte observou erros Signal classificados como `counter`, mas a bridge
serve várias empresas; não é prova de que os erros pertencem a esta conta.

## Procedimento com administrador principal
1. Entre no painel administrativo com a identidade de **administrador principal**
   e sessão/CSRF válidos. Abra **Diagnósticos do dispositivo** →
   **Revisão de mensagens retidas**.
2. Clique **Buscar dispositivos com mensagens retidas**, selecione o dispositivo
   identificado pelo nome da empresa e confira se o estado está atualizado.
3. Clique **Consultar entradas**. A consulta (dry_run) é somente leitura.
   Os resultados mostram horário, motivo e identificador não reversível
   (HMAC de 20 caracteres), **não exibem** telefone, LID, conteúdo ou chaves.
4. Confira o histórico diretamente no **WhatsApp oficial** e, quando possível,
   confronte o estado das ações no Orion (envios, comandos, pagamentos, agendamentos).
   Para execução incerta, NÃO suponha que nada ocorreu.
5. Revise no máximo **duas entradas por operação**, apresentando o SHA-256 do
   comprovante revisado, commit de 40 caracteres do software relacionado e
   horário real de conferência, com fuso. A decisão é **do_not_replay**; não
   reenvia nem executa ações antigas. Se não houver prova confiável, **não libere**.
6. Confirme a ação. Antes da mutação, é gerada e verificada cópia criptografada
   do diário, sem alterar o original. A mutação faz CAS do snapshot, registra
   operação, ator, evidências e SHA-256; preserva todo o conteúdo criptografado
   como *operator_retired*. Uma tentativa com o mesmo UUID é idempotente.
7. Repita para as demais entradas, atualize **Dispositivos** e confirme que
   `ingress_blocked=0`, sem `ingress_storage_unavailable`, e que o transporte
   está conectado. Verifique um atendimento **novo** de teste sem mensagens
   antigas ou envio duplicado, com consentimento e sem gerar cobrança real.
8. Havendo falta de prova, falha de backup, lease inválida, troca de sessão ou
   conflito de snapshot, a aplicação falha fechada: mantenha atendimento manual
   e investigue, sem apagar arquivos, marcar resultado falso ou reiniciar.

## Segurança e limites
- O navegador nunca recebe credenciais do canal, MASTER, telefone, LID ou mensagem.
- A API verifica a empresa ativa, conexão canônica, proprietário e lease vigente.
- A bridge exige chave MASTER, chave do canal, declaração temporal HMAC do ator
  de plataforma e conferência da posse atual. A operação tem máximo de 2 entradas.
- O diário pertence ao par empresa/canal/dispositivo/geração e à sessão criptografada
  (epoch); um login novo não herda entradas antigas. Revisões em sessão incorreta
  são rejeitadas.
- Entradas em processamento ativo não podem ser aposentadas. Mensagens já
  iniciadas exigem comprovação de revisão específica; nenhuma ação é repetida.
- A ferramenta não promete recuperação de conteúdo Signal não descriptografável
  e não substitui a apuração de `decrypt_failed/counter` por canal com logs seguros.
- Reinícios e reautenticações não constituem evidência de conclusão nem
  removem eventos bloqueados. O objetivo é restaurar novos atendimentos sem
  mascarar a revisão humana de pedidos anteriores.
