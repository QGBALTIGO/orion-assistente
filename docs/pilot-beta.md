# FATIA 5 — Piloto beta controlado (preparação, não produção)

O piloto real **NÃO foi executado**: não há consentimento documentado da
barbearia, canal Meta real, domínio aprovado, projeto Railway provisionado,
notificação de privacidade aprovada nem backup externo restaurado.

## Pré-requisitos obrigatórios antes de marcar como concluído

- Autorização escrita do responsável e termo sobre tratamento de dados;
- serviços, preços, equipe, pausas, férias, números autorizados confirmados;
- número Business elegível e autorização Meta/Cloud API comprovada;
- contas Google opcionais somente por OAuth dos titulares;
- ambiente Railway de homologação separado de produção, TLS e firewall;
- credenciais novas por papel, Fernet no gerenciador de secrets, rotação;
- backup cifrado offsite, teste de restauração, retenção definida;
- métrica de custo por mensagens/categorias e plano de suspensão;
- roteiro supervisionado de agendar, duplicar evento, confirmar, cancelar
  e remarcar, incluindo indisponibilidade simulada da Meta;
- análise LGPD, informação sobre responsável, finalidade e exclusão.

O programa scripts/pilot_preflight.py exige TODOS os sinais de controle,
falha com código 2 se faltar algum e não imprime nenhum valor secreto.
É um checklist, não substitui evidências reais ou aceite humano.

## Verificação de backup no CI
Um job GitHub Actions realiza pg_dump e restauração em banco efêmero
de testes. Isso verifica **apenas** restauração técnica local; NÃO prova
criptografia, armazenamento externo, recuperação da Railway ou RPO/RTO.
Não use o banco de dados do piloto para testes sintéticos.

## Pausa e rollback
META_LIVE_SEND e GOOGLE_LIVE_SYNC permanecem desabilitados por padrão.
Se houver incidente, suspenda canal pela interface operacional validada,
pause serviços e investigue sem apagar o banco. Nunca executar
docker compose down -v em produção. Nenhum tráfego de cliente final real
é autorizado por esta PR.
