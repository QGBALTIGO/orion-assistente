# Publicação autônoma de sites — 26/10/2026

Especificação: anexo `PROMPT ESPECIALISTA — ORION`, 326 linhas, lido integralmente. Repositório confirmado `QGBALTIGO/orion-assistente`. Base desta tarefa: `main` em `86b75d2919a9a7e820fe963ba29ae027c70785c7`. A branch anterior foi preservada; esta especificação solicita explicitamente a `main`. As mudanças concorrentes até `1e28250` foram integradas e o candidato voltou a ser testado.

## Índice de leitura e execução

Todas as seções abaixo foram lidas; a coluna de evidência será fechada com os testes desta tarefa.

| Seção | Linhas do anexo | Componente real / verificação |
|---|---:|---|
| 1. Autonomia | 14–41 | Publicação existente; autorização por associação owner, sem aprovação externa |
| 2. Bloqueios | 43–72 | `tenant_access`, `management_api/models`, `onboarding_service`, interface e Railway |
| 3. Publicação | 74–96 | Mesmo POST `/management/site/publication`; checklist técnico e confirmação do proprietário |
| 4. Isolamento | 98–121 | RLS forçada, associação a uma empresa, snapshots, histórico e namespace central |
| 5. Endereços | 123–143 | `/s/{slug}`, registro único, disponibilidade autenticada e aliases duráveis |
| 6. Versionamento | 145–163 | Rascunho separado, publicação transacional, histórico imutável e reativação |
| 7. Permissões | 165–180 | Owner publica; editor edita; viewer consulta; suporte diagnostica |
| 8. Interface | 182–203 | Estado real, pendências, última publicação, copiar/abrir/retirar/reativar |
| 9. Agenda | 205–219 | Motor existente, preços/durações/horários operacionais e visuais publicados |
| 10. Segurança | 221–233 | Assinatura, XSS, CSRF/origem, rate limit, RLS, concorrência, uploads existentes |
| 11. Escala | 235–247 | Resolução indexada, consultas em lote, nenhuma aplicação por empresa |
| 12. SEO | 249–257 | HTML com canonical/OG/JSON-LD por snapshot; prévia privada noindex |
| 13. Testes | 259–280 | PostgreSQL/Redis locais novos, empresas sintéticas e Chromium/WebKit |
| 14. Implantação | 282–294 | Testar antes; backup e saúde; nenhuma publicação automática de empresas |
| 15. Relatório | 296–326 | Este relatório distingue implementação, testes, produção e limites |

## Estado observado antes das alterações

- Railway API `616cba2f-23ac-4425-9c20-ca752825c62c` e worker `7d4ab8e3-0139-4291-93e1-60d95f66b1f7`: `SUCCESS`, ambos na `main` base acima. PostgreSQL, Redis e QR também `SUCCESS`.
- `/health`: HTTP 200. A página da demonstração ainda não publicada retornou 404, como esperado. Nada foi publicado para diagnosticar o botão.
- A interface autenticada aberta em Meu Site mostrava apenas a prévia e “A publicação é feita pelo proprietário”, mesmo com associação owner do próprio admin.
- Configuração inspecionada apenas em memória: assinatura dedicada ausente, fallback de assinatura válido, banco/Redis e perfis empresariais configurados. A ausência da chave dedicada não era a causa do bloqueio.
- Consulta de logs de publicação não retornou entradas; não se infere disso que uma tentativa de POST tenha ocorrido.
- VPS reservada não foi reativada. WhatsApp, dados e campanhas não foram usados para testes.

## Causas e correções

1. Papel global admin/support suplantava a associação owner; a interface também negava explicitamente esses papéis. A autorização de publicação agora se baseia na associação ativa da identidade à própria empresa. Admin sem essa associação não ganha publicação global.
2. Estatísticas e editor eram carregados no mesmo `Promise.all`; a falha das estatísticas derrubava Meu Site. Carregamento e estados de erro foram separados.
3. Unicidade de rascunho e publicação existia em colunas diferentes; não impedia colisão cruzada nem preservava links antigos. Um registro central de slugs mantém rascunhos, endereço canônico e aliases.
4. Retirada/reativação não distinguiam o snapshot anterior do rascunho atual. A reativação explícita preserva o rascunho e restaura a última versão pública.
5. Havia validação insuficiente de empresa ativa, vínculo serviço/profissional/horário e erros operacionais. O checklist usa fatos atuais e não exige agenda vazia ou revisão do Orion.
6. HTML de compartilhamento era genérico e catálogo fazia uma consulta extra por combo. Metadados são renderizados pelo servidor e componentes são buscados em lote.

## Contratos e preservação

- Continua sendo uma aplicação compartilhada. Não há deployment ou aprovação por empresa.
- GET autenticado `/management/site/publication` descreve permissões, pendências, versões e histórico; GET `/site/slug-availability` informa disponibilidade sem identidade de terceiros.
- POST existente continua exigindo `expected_version` e `enabled`; `restore_previous: true` reativa o snapshot anterior, somente quando retirado do ar.
- Preços, durações, vínculos ativos e horários permanecem operacionais. Nome, descrição, imagens e apresentação dos serviços/profissionais ficam na publicação. Uma entidade inativa nunca é reativada pelo snapshot.
- Publicações anteriores à migração mantêm o comportamento compatível até sua próxima republicação explícita.
- Um alias de uma empresa publicada redireciona o HTML para o endereço canônico; endpoints antigos continuam aceitando a mesma empresa e cotações assinadas. Site retirado do ar não revela dados por aliases.
- Sem cache público de disponibilidade, clientes ou sessões. Não há invalidação distribuída atrasada do estado da publicação: a resolução consulta o registro indexado atual.
- Subdomínios e domínios personalizados ficam preparados pelo registro central de identidade/endereço; provisionamento DNS/TLS e verificação de propriedade continuam futuros. Nenhum domínio inexistente é anunciado.
- Não há delegação específica de publicação no modelo atual de equipe; editores continuam sem publicar. Não foi introduzida uma permissão ampla para contornar isso.

## Migração e recuperação

`0036_site_publication`, sobre `0035_public_amenities`: acrescenta contador e data de retirada, registro de endereços e histórico com RLS forçada. Não publica sites, modifica agendamentos ou reinicializa sessões. Colisões legadas entre empresas abortam a transação antes do backfill; não são resolvidas renomeando dados silenciosamente.

O histórico não pode ser atualizado/apagado pelo runtime. Aliases publicados ficam reservados à empresa. O guard de escrita exige o marcador transacional definido automaticamente pelos dois writers novos; uma imagem antiga não pode alterar preferências nem deixar o registro de endereços incoerente durante a troca de versões.

A recuperação preferida é uma correção adiante, mantendo o schema. Reverter para imagem anterior deixa a edição/publicação de sites bloqueada pelo guard; não constitui rollback funcional completo. Reverter o banco exige manutenção, backup consistente e reconciliação de gravações posteriores, sem sobrescrever agendamentos novos com uma cópia antiga.

O ensaio exato do runner preservou dados e metadados de 85 tabelas em 0035 e 87 em 0036, incluindo ACL, owners, RLS, policies, sequências e triggers. Restauração exige os papéis existentes e o baseline de ACL do banco/schema e da extensão `btree_gist` sob seu owner antes do restore. O archive sozinho não exporta o owner dessa extensão. `exact-release-recovery-acceptance.json` registra essa condição e diferenças somente textuais do deparser de constraints; não se declara igualdade textual onde ela não existe.

`scripts/backup_and_migrate_site_publication.py` aceita exclusivamente `0035 → 0036`: dump privado preservando owners e ACL, validação do arquivo, upload no bucket já autorizado, checksum e leitura de volta precedem qualquer DDL. O comportamento dos backups antigos permanece igual. No final verifica owners, RLS, permissões das funções finitas, índices, colunas e triggers. Falhas recusam a nova versão. Backup nativo de volume foi recusado pela Railway porque exige plano Pro; não houve alteração de plano. Referência do provedor: [backups](https://docs.railway.com/volumes/backups).

## Evidências e pendências da execução

Evidências desta tarefa ficam em `C:\Users\kayky\Documents\Playground\orion-site-publication-20261026\`. Nenhum resultado histórico foi somado aos testes atuais. Recibos possuem escopos sobrepostos; os totais abaixo não devem ser somados como casos únicos.

| Verificação executada | Resultado / evidência |
|---|---|
| Proprietário sem admin, UI → HTTPS → banco → site e reserva | 2/2 Chromium/WebKit; `site-publication-final-e2e.xml` |
| Responsividade real | 320/768/1280 px, site/painel/reserva; `final-browser-artifacts` |
| Regressões visuais, respostas sintéticas | 88/88; `browser-public-regressions.xml` |
| Nova interface e regressões de catálogo/upload/cores no DOM | 84/84, incluindo 24 cenários novos |
| PostgreSQL, assinatura, publicação, aliases e reservas após o merge | 81/81; `merged-publication-acceptance.xml` |
| Guard e runner do release | 42/42 backend/guard e 17/17 contratos de backup/release; recibos sobrepostos aos demais |
| Frontend servido ao vivo e rotas da fachada após o merge | 248 passaram, 1 pulado; `merged-live-frontend.xml` |
| 20 empresas independentes, concorrência e isolamento | Casos do backend em banco real, incluídos nos recibos `backend-publication-*.xml` |
| Backup/restauração e migração, somente sintéticos | 85 tabelas idênticas; 2 sites legados preservados; colisão aborta sem alterações; `migration-recovery-acceptance-final.json` |
| Contrato e hardgate do schema final | `schema_contract_ready=True`; `final-schema-ready.json` e teste PG do guard |
| Suíte unitária ampla | 4511 passaram, 39 falharam, 2 pulados; as 39 falhas foram reproduzidas no archive imutável da base, com causas equivalentes: `baseline-39-comparison.json` |
| Suíte DOM ampla após o merge | 495/496; a única falha é a expectativa antiga de usar telefone como nome do cliente, também reproduzida na base |

Na primeira suíte DOM ampla, 4 falhas já existiam na base: três guardas Vercel com hash antigo e uma expectativa de nome de cliente. A comparação usou cópia imutável do HEAD original. O merge preservou a correção concorrente da fachada e os três casos Vercel passaram. A suíte ampla não deve ser descrita como totalmente verde.

## Critérios de aceite e limites

- A empresa proprietária publica e controla o próprio site após validações automáticas; nenhuma etapa solicita aprovação da administração.
- A migração e o deployment não publicam empresas automaticamente. A confirmação do usuário no painel continua obrigatória para cada publicação.
- Agendamentos, itens e histórico permanecem idênticos durante o ciclo publicar/republicar/retirar/reativar dos testes reais.
- Compartilhamento usa HTML específico da versão pública; imagens/textos de rascunho não aparecem no catálogo, resumo ou confirmação. Cotações novas assinam a versão visual; replay usa essa versão imutável.
- Mudança da composição de um combo exige republicação; o fingerprint inclui a ordem para que a interface reconheça a pendência. Aliases compartilham a quota do estabelecimento.
- Sem qualificação de carga de 10 mil usuários simultâneos ou mil sockets WhatsApp nesta tarefa. Vinte empresas sintéticas não demonstram essa capacidade.
- Subdomínios, domínios próprios e delegação específica de publicação para editores continuam fora do modelo atual, conforme as dependências descritas acima.
- Nenhuma mensagem, campanha, canal ou IA real foi usada nos testes. A conferência pós-deployment deve permanecer somente de leitura e constar do recibo final externo.

## Estado operacional

Este é o relatório versionado do candidato qualificado. O fechamento da implantação, revisão efetiva publicada, backup e comparação de integridade são registrados externamente em `orion-site-publication-20261026/relatorio-final.md` e seus recibos. Não confundir testes locais de publicação pública com publicação automática de um cadastro real.
