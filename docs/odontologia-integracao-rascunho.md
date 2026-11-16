# Orion Odontologia — Inventário e contrato de integração

**Base analisada:** arquivo `index(1).html` fornecido pelo proprietário, 864 linhas, contendo interface independente e JavaScript de demonstração. O ZIP complementar ainda requer inspeção byte a byte quando estiver disponível no ambiente de testes. **Não copiar este HTML/CSS/JS para produção.**

**Referência de produção:** `security/prelaunch-hardening-20261030` do repositório `QGBALTIGO/orion-assistente`. Frontend canônico: `app/static/orion-suite.js` e `app/static/orion-suite.css`. Agenda e cadastros já vivem no backend multiempresa do Orion.

## Objetivo e regras inegociáveis

- **Uma aplicação, um design**: o nicho seleciona módulos e nomenclaturas; componentes, tokens, tipografia Figtree/Outfit, espaçamentos, ícones, estados, modais, sidebar e responsividade continuam os oficiais do Orion.
- **Uma única agenda**: reutilizar `booking_service`, `booking_api`, histórico, confirmação, recorrência e motor de conflitos do PostgreSQL. Não implementar um motor paralelo a partir do protótipo.
- **Uma identidade do paciente**: o cadastro de paciente deve apontar para um cliente/identidade canônica, mas não pode unificar duas crianças ou um responsável e um dependente apenas porque compartilham telefone; exigir IDs distintos e relacionamentos explícitos.
- **Dados de saúde privados**: não usar `localStorage`, observações genéricas do CRM, WhatsApp, marketing, analytics, CSV genérico ou logs como substituto do prontuário. Criar armazenamento clínico privado com política por empresa e por papel, auditoria e retenção apropriada.
- **Sem diagnósticos fabricados**: ausência de preenchimento é `não registrado`, nunca `saudável`, `2 mm` ou `sem alergias`. IA só cuida de assuntos administrativos e encaminha questões clínicas a profissionais habilitados.
- **Sem navegação falsa**: módulos que ainda não passaram na validação de segurança, integração e testes não aparecem como funções clínicas disponíveis.
- **Sem publicação automática de saúde**: disponibilizar em produção apenas após testes técnicos, revisão profissional, validação de acesso e política de prontuário.

## Inventário do rascunho e destino no Orion

| Tela / funcionalidade do HTML | O que realmente faz no rascunho | Destino funcional planejado | Etapa |
| --- | --- | --- | --- |
| Dashboard | Estatísticas calculadas de arrays locais | `management/dashboard`, agenda e financeiro existentes; KPIs da clínica com consultas reais | 1 |
| Agenda diária/semanal | Grade 08–19, profissionais e salas fixos, checagem JS | Agenda real do Orion; depois reserva de consultório no servidor e exclusão de colisões | 1/2 |
| Pacientes | CRUD local, responsável em texto, WhatsApp | CRM real para dados administrativos; pacientes distintos, responsáveis, contato compartilhado e identificador próprio | 1/2 |
| Ficha do paciente | Abas resumo/evolução/consultas/planos/docs | Identidade + agenda reais; informações clínicas somente na área autenticada e segregada | 2/3 |
| Profissionais | Nome, especialidade, CRO fictício, consultório | Profissionais reais; especialidade, CRO/UF validável e permissões de prontuário | 2 |
| Procedimentos | CRUD de serviços em arrays e valores numéricos | Catálogo existente; categorias odontológicas, duração e preço em centavos; snapshots na reserva | 1/2 |
| Consultórios e recursos | Lista de salas sem persistência multiempresa | Cadastro por clínica, salas ativáveis, alocação concorrente e calendário de recurso | 2 |
| Jornada do paciente | Seis checkmarks pela existência de dados | Linha do tempo operacional com estados verificáveis; **não** equivaler a qualidade clínica | 2/3 |
| Prontuário | Notas no navegador, autoria textual fixa | Episódios, versões assinadas, autor real, carimbo temporal, auditoria, acesso e exportação | 3 |
| Anamnese | Questionário simplificado, texto livre | Formulário versionado, respostas declaradas, responsável quando aplicável e assinatura | 3 |
| Odontograma | 32/20 dentes FDI e cinco faces, SVG ilustrativo | Estados por dente e face, dentição, histórico/versionamento, assinaturas, sem diagnóstico automático | 3 |
| Periodontia | Seis sítios/dente com padrão fictício de 2 mm | 6 sítios por dente; ausentes = não registrados; sangramento por sítio, retração e indicadores clínicos | 3 |
| Exames e imagens | Lista apenas metadados, sem upload real | Upload autenticado e privado, checksums, acesso e visualização apropriada; DICOM exige projeto adicional | 3/4 |
| Planos de tratamento | Etapas por checkbox, dente, preço em reais | Etapas profissionais, autoria, autorização e orçamento versionados; não concluir sem processo verificável | 3 |
| Orçamentos e aceites | Marcador aprovado/recusado no navegador | Orçamento comercial versionado separado de consentimento clínico/assinatura | 3/4 |
| Convênios | Cards estáticos, cadastro simples fictício | Contratos, elegibilidade e tabelas; TISS/TUSS somente com versão vigente e homologação | 4 |
| Estoque/insumos | Entradas/saídas de quantidade genérica | Estoque real por unidade; lotes, validade, rastreio e esterilização por módulo especializado | 4 |
| Prótese e laboratório | Pedidos e progresso somente local | Ordens ligadas ao caso/paciente, prazo, responsável, anexos e entrega registrada | 4 |
| Financeiro | Somatórios, lançamentos locais, pago/pendente | Livro financeiro real do Orion, com orçamento e procedimento vinculados sem vazar dados clínicos | 1/4 |
| Recepção IA/WhatsApp | Conversa local com `if` por palavra-chave | Central e WhatsApp existentes; apenas informações administrativas sem diagnóstico ou dado sensível | 1/4 |
| Automações | Switches locais sem envio | Políticas reais de lembretes e confirmações, autorização por finalidade e destinatário | 4 |
| Relatórios | Contagens de arrays e CSV demonstrativo | Consultas agregadas por período e equipe, sem exportação clínica indiscriminada | 1/4 |
| Site público | Mock de navegador e agendamento fictício | Site e motor público existentes; sem expor prontuário, odontograma ou anexos | 1/4 |
| Configurações | Switch local e itens de segurança “planejados” | Preferências reais e configuração de papéis; nunca tratar switch de UI como autorização | 2/3 |
| Referências externas | Links e licenças supostas, bibliotecas não instaladas | Avaliar licenças individualmente; aproveitar ideias, não copiar código de GPL/BSL para produto proprietário sem revisão | Pesquisa |

## Defeitos identificados no HTML de demonstração

1. `localStorage` armazena todos os dados; qualquer pessoa com o navegador pode adulterar/ler o conteúdo sem autenticação e sem tenant real.
2. `getPatient(id)`, `getDoctor(id)` e `getService(id)` retornam o **primeiro registro** quando o ID não existe. Isso pode associar consulta, pagamento e anotação ao paciente errado. Em backend real, falhar com 404 e não redirecionar silenciosamente.
3. `nextId(prefix, arr)` usa `arr.length+1`; exclusões e concorrência podem reciclar identificadores. IDs reais: UUID e controle de idempotência.
4. A checagem `checkConflict` não é transacional, usa limite 08–19 mesmo quando o profissional tem outro expediente; não impede duas requisições concorrentes. Reutilizar exclusão PostgreSQL do Orion.
5. O cadastro admite telefone compartilhado, enquanto o CRM anterior tende a identificar clientes por contato; exige relação responsável/dependente para não mesclar prontuários.
6. `modalDoctor` usa CRO fictício pré-preenchido. Campo profissional não pode assumir validade, deve ser verificável e vinculado à identidade autorizada.
7. A tela periodontal mostra `2 mm` como medição de sítios **não preenchidos**; isto pode fabricar achados clínicos e distorcer estatísticas.
8. O estado geral de um dente é atualizado pela última face alterada, podendo ocultar outra face previamente afetada. Persistir achados por sítio e calcular resumo não destrutivo.
9. A conclusão de plano é um checkbox sem autoria, tempo, aprovação, recusa, justificativa ou assinatura; não constitui atendimento realizado.
10. O armazenamento de exame adiciona somente um rótulo; não há bytes, verificação de formato, proteção de URL ou logs de acesso.
11. A simulação de IA procura expressões regulares na última mensagem, não consulta agendas ou protocolos reais e não autentica o paciente.
12. Histórico de auditoria local tem limite de 100 linhas e pode ser apagado; qualquer trilha real deve ser append-only no servidor e fora de alterações comuns.
13. CSS define/redefine tokens em múltiplos blocos contraditórios e insere estilos de demonstração próprios. Importar o design causaria divergência do Orion.
14. `renderProcedures`, `renderPerio` e extensões adicionais sobrescrevem funções globais do script; o frontend oficial do Orion utiliza outras rotinas. Reescrever por módulos próprios, sem copiar wrappers do protótipo.
15. Mais de vinte itens de menu em tela pequena geram densidade e navegação confusa. Mostrar módulos por estágio e agrupar, mantendo um menu responsivo e o dock oficial quando disponível.

## Etapas de implementação, uma por vez

### Etapa 1 — integração operacional sem dados clínicos novos

**Primeira entrega nesta branch:** contexto `business_niche=odontologia` derivado no servidor, menus organizados por área, labels de Pacientes/Procedimentos/Equipe clínica e Dashboard usando as APIs reais já existentes; ícone de dente próprio; nenhum componente de estilo externo importado. Regras de plano e modo Aplicativo permanecem iguais. Rodar DOM, regressão e testes de contexto.

### Etapa 2 — identidade do paciente, profissional, consultórios e agenda

Implementar pacientes individuais com responsável explícito, normalização de contato que não mescle identidades, validação de profissional CRO/UF e consultórios ligados a reservas. **Atomicidade**: gravar horário e sala na mesma transação, detectar colisão por profissional e por consultório e suportar remarcação/cancelamento. Cobrir confirmação, manual, recorrência, múltiplos serviços e migração de histórico.

### Etapa 3 — prontuário e módulos odontológicos

Criar esquema `dental_*` ou tabelas separadas por tenant + paciente com RLS forçada, permissões por profissional vinculado, revisão e assinatura, histórico imutável, snapshots, auditoria de leitura e escrita. Implementar anamnese, odontograma FDI, periodontograma, evolução clínica, exames e tratamento; integrar UX exclusivamente com os tokens e modais do Orion. Não reutilizar `notes` do CRM para saúde.

### Etapa 4 — integração operacional completa e homologação

Conectar orçamento/aceites, convênios, próteses e laboratório, estoque rastreável, agendamentos públicos, lembretes administrativos, financeiro, relatórios e interoperabilidade. Verificar regras de licenciamento dos repositórios citados no HTML antes de instalar qualquer dependência.

## Critérios objetivos de aceite

- Mobile 320, 360, 390, 430 px, tablet 768/1024 px, desktop 1280/1440+ px: **sem largura extra não intencional, texto cortado, sobreposição, barra de modal inacessível ou menu atrás do overlay**. Testar tema claro, escuro e sistema com tokens oficiais.
- Foco por teclado, nomes acessíveis, estados carregando/erro/vazio, cliques rápidos (mínimo 100 alternâncias), transições em acessibilidade com movimento reduzido.
- Testes de integração com PostgreSQL real e RLS: duas empresas, funcionário não autorizado, tentativa de ID cruzado, sessão expirada, permissão revogada e não exposição em CSV, WhatsApp e site público.
- Teste de concorrência de dois agendamentos na mesma sala, inclusive com dentistas diferentes; transações devem impedir sobreposição no banco.
- Os registros de saúde não podem aparecer em notificações, logs, métricas, central de vendas, páginas públicas ou mídia acessível por URL sem autorização.
- Backup/restauração com dados sintéticos, ensaios de migração e rollback seguro, testes de regressão em barbearia e assinaturas antes de publicar.

## Referências regulatórias para a revisão especializada

O **CFO** disponibiliza o [Manual do Prontuário do Paciente em Odontologia](https://prescricao.cfo.org.br/) e orientações para documentação e assinatura. A **ANPD** classifica dados de saúde como pessoais sensíveis ([Perguntas frequentes da ANPD](https://www.gov.br/anpd/pt-br/acesso-a-informacao/perguntas-frequentes/perguntas-frequentes)). Para convênios, usar o [Padrão TISS de setembro de 2026](https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/padrao-tiss-setembro-2026) e confirmar a vigência no momento da homologação.

**Importante:** este documento é uma especificação de engenharia, não substitui validação por cirurgião-dentista, análise de privacidade/jurídica e homologação de operadoras.
