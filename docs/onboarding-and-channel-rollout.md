# Configuração inicial e conexões independentes

Base: `dee6aaac4382bb12e5f8660b092ee09812125a87`. Trabalho em branch isolada.
Uma publicação exige autorização explícita do operador e aprovação da bateria
da revisão exata a publicar. A autorização posterior de publicação não ativa
campanhas, integrações ou sessões individuais sem a revisão de seus responsáveis.

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
deduplicação e lease separados. A autorização acompanha a transação de reservas e
pedidos até a gravação final. O aviso de interação com IA desligada usa uma guarda
própria, limitada a invalidar automações: proprietário, canal, empresa, remetente e
lease são revalidados na mesma transação, com locks até o commit. Se o lease vence,
a invalidação inteira volta atrás e a API não confirma o registro do aviso. Essa
guarda não ativa o assistente nem permite reservas ou pedidos.
Não apagar `/data/auth`, sessões, filas ou reservas no retorno de versão.

O suporte gerenciado inclui texto e áudio privados com allowlist restrita,
anúncio de revisão antes de transcrição e retry store cifrado por canal.
Isso não representa homologação física de áudio, grupos ou paridade de todas
as funcionalidades do conector legado.
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

Reposição de vagas permanece **indisponível, em rascunho**. O banco atual não tem
fila de espera com preferências, consentimento específico, oferta com validade e
aceitação concorrente. `vacancy_requested` conserva somente uma preferência de
configuração. A API declara `features.vacancy.available=false` e `enabled=false`;
não cria contatos, convites, retenções ou reservas para esse pedido. Enquanto ele
estiver marcado, habilitar execução retorna conflito e a autorização anterior não
permite planejar novos envios. O proprietário pode pausar, desmarcar a intenção e
revisar as demais rotinas. Jobs com evento não implementado nunca são produzidos
nem enviados. Uma campanha manual não se transforma em lista de espera.

## Homologação isolada

1. Preparar backup, banco, Redis e volumes exclusivos de teste. Aplicar Alembic até
   `0023_automation_notice_guard` com migrador e verificar RLS. Scripts antigos fixados
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

Os testes `test_automation_notice_postgres.py` exercitam o papel runtime e disputas
reais de revogação/fence/expiração, com contatos sintéticos e sem transporte.
Coleção ou execução pulada localmente não equivale a aprovação PostgreSQL; a CI
deve executar a cadeia inteira e esses casos antes da publicação.

## Retorno e evidências

Pausar empresa, campanha ou contato interrompe novos pendentes. Não excluir dados
para retornar. Manter leitores compatíveis e reconciliação de tentativas em curso.
As migrations recusam retorno destrutivo quando há dados novos.

Separar unidade, DOM simulado, PostgreSQL/Redis reais, navegador, aparelho, Google
e carga sustentada. Ver os contratos e limites em
[navegador](qualification-e2e.md), [conversas](qualification-conversation-manifest.md)
e [carga](qualification-load.md). A existência dos runners não comprova aprovação:
guardar commit, árvore, durações, percentis, erros e invariantes de cada execução.
Falhas de carga impedem o aceite, mesmo com unidade e navegador aprovados.
Janelas de 12 horas, sockets WhatsApp e entrega externa exigem evidências próprias.

## Publicação com preservação de dados

Usar `scripts.one_time_onboarding_migration` no predeploy da API com um único
run ID estável. O runner confere a cadeia selada 0019–0023, salva backup cifrado
fora do container, verifica sua leitura e restaura uma cópia em outro banco.
Somente após ensaiar o upgrade dessa cópia e comparar os dados/ACLs da origem
ele aplica as migrations. Não restaura sobre a origem nem executa seed.
Repetir uma execução já concluída faz conferência somente de leitura.

O worker aguarda `scripts.wait_for_onboarding_schema`, com prazo limitado e
papel runtime; não migra por conta própria. Publicar API, worker e bridge com
contratos compatíveis, preservando `/data/auth` e a chave Fernet. Revalidar
saúde, configuração Google e sessões depois da publicação. Resultado aceito
por transporte não substitui a leitura no aparelho autorizado.
