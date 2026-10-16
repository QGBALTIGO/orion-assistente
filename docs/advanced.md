# FATIA 8 — Arquitetura avançada e limites

**Status:** schema/API interna e testes sintéticos, NÃO produto avançado em
produção. O documento mestre condiciona esta fatia à aprovação comercial e
ao piloto validado. Esses eventos ainda não ocorreram.

## Entregue no código

- Migração 0007: `human_handoffs` com FK por tenant/canal;
  `contact_consents` opt-in/out separado de agenda; campanhas em rascunho;
  destinatários referenciando consentimentos do tenant; workflows
  declarativos e desativados. RLS FORCE e papéis runtime/migrator distintos.
- Atendimento humano: "ATENDENTE" grava solicitação no banco e bloqueia
  menus por 30 minutos. Inbox operacional lista e resolve tickets sob
  autenticação do suporte; não expõe outro estabelecimento ao leitor.
- "SAIR" suspende marketing para o telefone remetente e apaga estado da
  conversa em Redis.
- Campanhas: rascunhos validados, contador de contatos com consentimento,
  endpoint de envio bloqueado (HTTP 423); nenhuma mensagem externa.
- Workflow: aceita apenas operações declarativas tag/handoff/reminder_draft,
  e grava `enabled=false`. Não existe executor de fluxos arbitrários.
- Guard de funções para uso futuro de IA aceita somente consulta de
  disponibilidade/ajuda com tenant do servidor; recusa SQL, tokens,
  `tenant_id` fornecido pelo modelo e chamadas diretas de reserva.
- Mock de transcrição recusa tipos/tamanhos inválidos e retorna texto
  identificado como fictício; não chama IA real.
- Adaptador Chatwoot intencionalmente indisponível sem provedor autorizado.

## Não entregue / exigências antes de habilitar

1. **Chatwoot real:** instalação autorizada, segregação por organização,
   contrato/licença da versão selecionada, conexão autenticada e teste
   de handoff e de falhas operacionais.
2. **IA e áudio real:** consentimento, retenção, proteção dos dados,
   integração com fornecedor, políticas de orçamento/tokens, cotas por
   estabelecimento, verificação de horários via motor FATIA 2 e
   avaliação de qualidade, privacidade e prompt injection.
3. **Campanhas reais:** Meta templates aprovados, janelas de atendimento,
   autorização do titular do número, comprovação de opt-in, opt-out,
   limites por segmento e teto de custos; envio em massa não autorizado
   permanece indisponível.
4. **Editor React Flow/Activepieces:** revisão de licença e segurança,
   engine transacional, histórico/reversão, execução isolada por tenant.
5. **Fidelidade/aniversários:** informação de data de nascimento opcional,
   política de retenção/consentimento e relatório de direitos LGPD.
6. **Self-service e planos avançados:** pagamento sandbox/real validado,
   aprovações comerciais, elegibilidade Meta e onboarding supervisionado.

## Critérios de aceite futuro

Piloto FATIA 5 aprovado por responsável; CI sem falhas; testes reais
Chatwoot/Meta/Asaas/OAuth; audit trail e RLS para todas as tabelas;
monitoramento de incidentes, autenticação de operador por sessão/MFA e
execução de campanha autorizada contra sandbox restrita com opt-out.

A ausência de integração externa, de aprovação do piloto e de capacidade
validada torna incorreto declarar a FATIA 8 concluída comercialmente.

## Comandos WhatsApp protegidos (complementação da FATIA 3)

Quando o evento tem assinatura Meta validada e canal identificado pelo
\`phone_number_id\`, a identidade do remetente é comparada com
\`authorized_staff.phone_e164\` ativo no tenant. Barbeiro com
\`professional_id\` vinculado vê apenas sua agenda; proprietário e
assistente autorizados podem consultar a agenda da empresa.

AGENDA HOJE / AGENDA AMANHÃ são comandos apenas para staff.
PAUSAR BOT / RETOMAR BOT são exclusivos do owner e exigem confirmação
em até dois minutos. BLOQUEAR AAAA-MM-DD HH:MM-HH:MM exige
profissional vinculado e confirmação; revalida reservas e registra
auditoria sob advisory lock transacional.

Cliente tem MEUS HORÁRIOS, CANCELAR AG-XXXXXX (com SIM CANCELAR) e
REMARCAR AG-XXXXXX. O protocolo não serve como senha:
toda consulta e alteração valida novamente que o número Meta assinado
é o titular da reserva. Histórico e escopo RLS são preservados.

**Não aceitar esses comandos por canal não autenticado.**
