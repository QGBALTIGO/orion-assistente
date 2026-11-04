# Etapa 06 — qualificação de Diagnósticos

## Base, escopo e limites

Base publicada: PR 252, commit `f58730ac8e380bd2c12286c69c1db3873d3cbf0f`,
árvore `28ac47dc292556b77b80fbc80607077265011a41`. O commit local equivalente
é `f7063173988ae8b84883fd42d1e52fdbd4a092ea`; os hashes de commit diferem,
mas a árvore é idêntica. A qualificação compara o conteúdo publicado e o novo
patch, sem tomar a branch `main` antiga como produção.

Módulo restrito a `servico_assinaturas`, com permissão `diagnostics.manage`.
A observação, seus limites e o efeito automático inesperado da referência estão
em [diagnostics-observation.md](diagnostics-observation.md). O Orion exige
confirmação explícita para a mensagem de teste. Nenhum envio, probe ou alteração
de configuração real é feito pela qualificação deste patch.

O [contrato técnico](diagnostics-contract.md) e a fixture compartilhada
`tests/fixtures/subscription_diagnostics_contract.json` definem os mesmos DTOs
para Python, DOM e navegadores. PostgreSQL, Chrome oficial e WebKit dependem
dos runners isolados do CI; coleta e compilação locais não substituem execução.

## Evidência local antes da PR

| Grupo | Resultado | Cobertura relevante |
| --- | --- | --- |
| Interface e consumidores compartilhados | 281 testes passaram | 30 novos de Diagnósticos, módulos anteriores, navegação, autorização e contexto da conta. |
| Ponte e consumidores compartilhados | 375 testes passaram | Operação de proxy independente de sessão, DNS público e IP fixado, CONNECT/TLS, headers/tempo limitados, self-send confirmado, revogação e recibos. |
| Conjunto Python afetado | 1.368 testes passaram, 6 avisos de depreciação já existentes | Contratos e consumidores dos módulos anteriores, incluindo os 82 testes funcionais e os guards de publicação abaixo. |
| API funcional de Diagnósticos | 82 testes passaram | Corpo limitado, telefone canônico, leituras sem escrita, DTO seguro, expiração, confirmação, broker, tenant ativo, recuperação e recibos antigos. |
| Publicação 0066 e cadeias anteriores | 141 testes passaram | Backup privado obrigatório, origem revisada, validação de catálogo, credenciais restritas e ordem de inicialização. |
| PostgreSQL e navegadores | 80 casos coletados | 45 PG funcionais, 13 PG de release, 22 browsers (11 por engine); execução pendente no CI. |
| Servidor HTTP sintético | Smoke em loopback concluído | Assets e contratos reais, preparação/confirmação/proxy com resposta perdida e recuperação GET; um efeito e um probe sintéticos. |

O conjunto Python completo foi executado sobre a árvore final em 60,71 s.
Os números de grupos sobrepostos não devem ser somados como casos únicos.
Os testes de ponte usam sockets falsos ou proxy/TLS de loopback com certificado
descartável; não consultam o alvo externo real.

Foi eliminada uma corrida no teste DOM de permissão: a asserção de primeira
consulta passou a aguardar a chamada observável, em vez de presumir que o hash
assíncrono da identidade terminou depois de um número fixo de ciclos do event loop.
Não foi reduzida a asserção nem alterado o comportamento do produto para passá-la.

## Gates obrigatórios do CI

O workflow `subscription-chatbot.yml` inclui os seis grupos anteriores e os
novos caminhos de Diagnósticos, a migração 0066, os scripts de release, os testes
PG, o componente DOM e os fluxos reais de navegador.

- Python: contratos públicos/privados, dispositivos, chatbot, templates,
  histórico, permissões, QR e publicação.
- Ponte: todos os testes afetados `device-*`, `chatbot-*`, `channel-*` e seus
  consumidores compartilhados.
- DOM: assets reais e API sintética, recuperação em reload, troca de conta,
  empresa e nicho, logout, confirmação/cancelamento e escapes de conteúdo.
- PostgreSQL: banco descartável com papéis `app_migrator`, `app_support` e
  `app_runtime`; nenhuma rede externa. Conferir ausência de escritas em GET,
  efeitos únicos sob concorrência, RLS/ACLs e catálogo da 0066.
- Chrome oficial e WebKit: três tamanhos, dois temas, seleção/Atualizar sem
  admissão, preview, cancelamento, confirmação, resultados, resposta perdida
  e recuperação; conferir limites horizontais e verticais dos diálogos.

Falhas históricas gerais não são apresentadas como testes aprovados. O baseline
qualificado da etapa 05 contém 24 workflows e 62 jobs (6 específicos e 56 gerais).
A comparação geral preserva fase, ID e multiplicidade; falhas sem ID exigem
comparação de comando, etapa, stack e causa. Repetição por falha de infraestrutura
deve conservar a evidência do primeiro resultado e o mesmo conteúdo testado.

## Publicação e conferência

Status deste registro: implementação em qualificação; merge e publicação ainda
dependem dos gates acima. A PR deve registrar head, checkout de merge sintético,
árvore, resultados finais e comparação completa antes do merge.

A publicação usa a branch `security/prelaunch-hardening-20261030`. É necessário
conferir a mesma revisão nos serviços API, worker e ponte; backup privado
verificado e migração 0065 → 0066; barreira de catálogo pronta; e navegação no
Chrome autorizado. A conferência ao vivo será somente leitura, sem confirmar
mensagem ou iniciar probe real. Campanhas (etapa 07) permanece fora deste patch.
