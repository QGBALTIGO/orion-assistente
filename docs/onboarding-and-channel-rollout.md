# Configuração inicial e conexões independentes

Base: `dee6aaac4382bb12e5f8660b092ee09812125a87`. Trabalho em branch isolada.
Produção, migrations reais, publicação e mensagens reais ficam fora da execução
automática desta tarefa, conforme o documento de implementação.

## Fontes de dados e permissões

O cadastro guiado reutiliza contas, empresas, memberships, profissionais, catálogo,
horários, pausas e perfil de atendimento existentes. As tabelas novas guardam
progresso e operações idempotentes, sem duplicar o catálogo. A revisão confere
versão e fingerprint dos fatos. Alterações em outro cadastro exigem nova revisão.

Empresas novas começam pausadas e itens novos inativos até conclusão autorizada.
Endereço operacional é privado. Concluir não publica site, conecta WhatsApp/Google
ou autoriza IA. Empresas antigas mantêm suas operações; leitura e cancelamento
autorizado não são bloqueados pela nova configuração. Convites não concedem papel
de proprietário.

O registro `qr_channels` vincula empresa, proprietário e provedor. Credenciais são
cifradas, com valor mostrado uma vez. Proxies autenticados evitam devolver chaves
salvas ao navegador. Cada canal tem sessão, diretório, contexto, intake, recibos,
deduplicação e lease separados. Perder lease ou permissão impede trabalho atrasado.
Não apagar `/data/auth`, sessões, filas ou reservas no retorno de versão.

O suporte inicial gerenciado é texto privado e allowlist restrita. Não representa
homologação de áudio, grupos ou paridade de toda recuperação do conector legado.
Abrir a página ou reiniciar processo não inicia sessões. Ver [CHANNELS.md](../qr-bridge/CHANNELS.md).

## Google Agenda

`python -m scripts.check_google_setup` verifica a preparação da API;
`python -m scripts.check_google_setup --worker`, do worker. Não faz chamadas Google
nem escreve configuração. Código 0 indica preparação local; 2, pendências.
Nenhum comprova autorização ou sincronização real.

São necessários cliente OAuth, segredo, chave Fernet estável e callback HTTPS
compatível com a origem pública. Depois, o proprietário autoriza a conta e escolhe
um calendário com edição. A integração atual envia do Orion para Google; não
importa alterações externas de volta. Ver [OAuth oficial](https://developers.google.com/identity/protocols/oauth2/web-server)
e [CalendarList](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list).
Não trocar uma chave Fernet existente: isso impediria decifrar tokens anteriores.

## Automações revisadas

Campanhas, destinatários e consentimentos existentes continuam sendo os registros
do negócio. A nova outbox armazena eventos, versões e tentativas. Consentimento
operacional e de marketing são independentes. Salvar regras não autoriza envios.
O proprietário revisa canal, janela local de 9h–18h e limite diário; cada campanha
exige revisão própria de conteúdo e público. Fatos ausentes não são inventados.

Resposta do contato, pausa humana, consentimento retirado, alteração de agenda,
campanha ou autorização invalidam os pendentes. A tentativa é persistida antes do
envio. Timeout e queda ficam incertos: consultar a operação original não reenvia.
Aceitação pelo conector, entrega e leitura informadas são estados distintos.
Legibilidade física exige teste no aparelho.

## Homologação isolada

1. Preparar backup, banco, Redis e volumes exclusivos de teste. Aplicar Alembic até
   `0022_marketing_execution` com migrador e verificar RLS. Scripts antigos fixados
   em outra revisão não aplicam esta cadeia automaticamente.
2. Na API de teste: `BUSINESS_PROFILES_ENABLED=1`, `ONBOARDING_ENABLED=1`.
   Verificar conta nova, convite, legado e múltiplas empresas.
3. Na API e bridge de teste: `QR_CHANNELS_ENABLED=1`, chave estável e
   `QR_CHANNEL_BROKER_URL` apontando para API interna isolada. Criar e ativar canal
   somente por ação do proprietário. Autorizar assistente separadamente.
4. Configurar `QR_BRIDGE_URL`. Somente após testes, habilitar na API e worker de
   homologação `MARKETING_EXECUTION_ENABLED=1` e
   `MARKETING_RECONCILIATION_ENABLED=1`. Manter reconciliação ativa ao pausar novos
   envios. Começar com transporte simulado e dados sintéticos.
5. Revisar consentimentos, regras e campanha. WhatsApp, OAuth/evento Google e
   carga externos reais pertencem a uma etapa própria, sem ativação automática.

## Retorno e evidências

Pausar empresa, campanha ou contato interrompe novos pendentes. Não excluir dados
para retornar. Manter leitores compatíveis e reconciliação de tentativas em curso.
As migrations recusam retorno destrutivo quando há dados novos.

Separar unidade, DOM simulado, PostgreSQL/Redis reais, navegador, aparelho, Google
e carga sustentada. Os perfis de 1.500 usuários, 500 casos conversacionais, 10.000
sequências e janelas longas do documento mestre não são comprovados por esta
implementação inicial.
