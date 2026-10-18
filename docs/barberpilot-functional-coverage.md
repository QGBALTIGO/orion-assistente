# Cobertura funcional: BarberPilot → Orion

Este documento registra a cobertura da adaptação no código do Orion, com referência à inspeção de 18 de outubro de 2026. A base do trabalho é o commit `3f171babc4e7e5d0736442403bfdb49dab513264` do `QGBALTIGO/orion-assistente`, acrescido das alterações de gestão desta entrega. Não constitui comprovação de equivalência integral, identidade visual pixel a pixel ou funcionamento em produção.

A implementação reúne navegação, agenda, clientes, serviços, equipe, financeiro, desempenho, fidelidade, site público e ferramentas de IA. Algumas funções são adaptações das regras do Orion; outras persistem somente rascunhos. Há lacunas funcionais identificadas abaixo. Não se atribui um percentual de conclusão: a existência de uma tela não comprova todas as suas ações, estados ou integrações.

O [documento principal](barberpilot-orion.md) reúne o contexto, desenho e resultados gerais. O [runbook da gestão](management-suite-runbook.md) descreve configuração, migrations, validação e operação. Esta matriz concentra-se na cobertura por função.

## 1. Como ler a evidência

| Marca | O que sustenta | O que não sustenta |
|---|---|---|
| **UI** | Estado realmente aberto no BarberPilot, registrado em captura estruturada de DOM, controles, estilos computados e retângulos. Há amostras de desktop, tablet e celular. | Resultado de salvar, enviar, pagar, conceder acesso ou concluir um atendimento que não foi executado na referência. Um estado vazio não demonstra a apresentação de todos os estados preenchidos. |
| **Estático** | Estrutura, campos, condições e mensagens presentes nos assets públicos, examinados de forma inerte com REA e extração de AST. | Regra efetiva do servidor BarberPilot, permissões da API, persistência, entrega de mensagens, cobrança ou uso real de IA. |
| **Código Orion** | Rota, formulário, validação, consulta ou mutação implementada neste repositório. | Disponibilidade de credenciais, serviços externos, deploy ou operação real da conta. |
| **Teste Orion** | Cenário automatizado específico, com dados sintéticos; os testes de gestão, recorrência, fidelidade e quitação usam PostgreSQL real com papéis separados e isolamento entre empresas. | Cobertura de todos os estados possíveis ou validação de provedores externos. Os testes de IA usam transporte controlado; não comprovam uma geração real do provedor em produção. |

**Situações usadas na matriz:** **Integrado** indica tela conectada a dados e operações reais; **Adaptado** indica comportamento implementado com uma regra ou fluxo próprio do Orion; **Parcial** indica uma parte funcional e outra ausente; **Rascunho** indica persistência sem execução externa; **Ausente** indica função sem implementação nesta entrega. Nenhuma dessas marcas substitui o resultado da última execução dos testes, registrado no documento principal/runbook.

### Fontes da inspeção

Os nomes abaixo identificam as evidências da sessão em `barber_research/` e `rea_review/`. As capturas autenticadas brutas ficam fora deste documento: podem conter identificação de conta ou dados do negócio. A matriz não reproduz nomes, e-mails, telefones, endereços, clientes ou textos integrais de artigos da referência.

| ID | Capturas UI relevantes | Complemento estático |
|---|---|---|
| **REF-SHELL** | `mobile-menu.json`, `dashboard-{desktop,tablet,mobile}.json`, `accelerate-{desktop,tablet,mobile}.json` | `app-BwudNkCd`, `app.dashboard-CqJeA5ou` |
| **REF-AGENDA** | `agenda-desktop.json`, `agenda-calendar-{desktop,tablet,mobile}.json`, `agenda-professional-desktop.json`, `agenda-tablet-mode-desktop.json`, `agenda-new-{desktop,tablet,mobile}.json`, `agenda-block-repeat-desktop.json` | `app.agenda-DBcMK1jO`, `agenda-shared` |
| **REF-CLIENTES** | `clients-{desktop,tablet,mobile}.json`, `clients-new-{desktop,tablet,mobile}.json` | `app.clientes-DZhK-63g`, `clientes-shared` |
| **REF-SERVICOS** | `services-{desktop,tablet,mobile}.json`, `services-new-{desktop,tablet,mobile}.json`, `services-combo-desktop.json` | `app.servicos-1KEBZWGN`, `servicos-shared` |
| **REF-EQUIPE** | `staff-{desktop,tablet,mobile}.json`, `staff-new-{desktop,tablet,mobile}.json`, `staff-role-permissions-desktop.json` | `app.funcionarios-BnRXj5_W`, `funcionarios-shared` |
| **REF-FINANCEIRO** | `finance-{desktop,tablet,mobile}.json`, `finance-new-desktop.json`, `finance-dre-desktop.json`, `finance-advance-desktop.json`, `performance-{desktop,tablet,mobile}.json` | `app.financeiro-BzUgi9-z`, `financeiro-shared` |
| **REF-FIDELIDADE** | `loyalty-{desktop,tablet,mobile}.json`, `loyalty-config-desktop.json`, `loyalty-share-desktop.json` | `app.fidelizacao-J_OgP9X7`, `fidelizacao-shared` |
| **REF-MARKETING** | `marketing-{desktop,tablet,mobile}.json`, `marketing-automations-{desktop,tablet,mobile}.json`, `marketing-campaigns-desktop.json`, `marketing-queue-desktop.json`, `marketing-recovery-desktop.json`, `marketing-autopilot-desktop.json`, `advisor-{desktop,tablet,mobile}.json` | `app.marketing-BP75DzpN`, `app.marketing.index-BYyYP76_` |
| **REF-SITE** | `site-{desktop,tablet,mobile}.json`, `site-settings-{desktop,tablet,mobile}.json`, `site-banner-desktop.json` | `configuracoes-shared` e funções reutilizadas do site |
| **REF-PUBLICO** | `public-{desktop,tablet,mobile}.json`, `public-booking-service-{desktop,tablet,mobile}.json`, `public-booking-hours-{desktop,tablet,mobile}.json` | Assets públicos observados; a submissão final da referência não foi acionada |
| **REF-CONFIG** | `settings-{desktop,tablet,mobile}.json`, `settings-hours-desktop.json`, `settings-appearance-desktop.json`, `settings-notifications-{desktop,tablet,mobile}.json`, `notifications-desktop.json`, `profile-{desktop,tablet,mobile}.json` | `app.configuracoes-D7Zmgmhx`, `configuracoes-shared` |
| **REF-CONTEUDO** | `plan-{desktop,tablet,mobile}.json`, `plan-annual-desktop.json`, `help-{desktop,tablet,mobile}.json`, `help-article-desktop.json`, `news-{desktop,tablet,mobile}.json` | Declarações de produto; contratação, suporte e push não acionados |

`REA_STATIC_REPORT.md`, `rea_run_summary.json` e `ui_static/` registram as fontes e os limites da análise estática. Os registros de AST relacionam fatos aos assets, hashes e posições de origem. Funções encontradas em um bundle não foram tratadas automaticamente como abas ativas: por exemplo, Serviços, Funcionários e Meu Site são rotas próprias; funções reutilizadas do bundle de Configurações não justificam inventar abas adicionais do BarberPilot.

As capturas do dashboard registram viewports de **1440 × 1000**, **820 × 1180** e **390 × 844**. São amostras de navegador, não testes em todos os modelos físicos de computador, tablet e celular. Cores, tipografia, dimensões e diferenças de composição pertencem à avaliação visual descrita no documento principal.

## 2. Navegação, Dashboard e Acelerar

Fontes: REF-SHELL. Implementação: [suíte de interface][UI-ORION], [CSS][CSS-ORION], [consultas de gestão][DADOS]. Testes relevantes: [gestão][T-GESTAO] e [interface][T-UI].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI + Estático:** menu lateral expandido/recolhido, menu móvel, cabeçalho, marca, atalhos e tema claro/escuro/sistema. | Shell de gestão com marca Orion, navegação responsiva, seletor de empresa autorizada, tema por dispositivo, diálogo com foco e retorno ao acionador. | **Adaptado.** A marca e os textos foram adequados ao Orion. As verificações de contrato não provam identidade visual de cada estado. |
| **UI:** dashboard com saudação, novo agendamento, novo cliente, acesso à agenda e indicadores do dia. | Atalhos abrem os fluxos reais. Indicadores vêm de agendamentos/clientes persistidos; a API agrega status, receita de concluídos, projeção, ticket, serviços e profissionais no fuso da empresa. | **Integrado.** Fórmulas e origem dos dados são as do Orion; o backend BarberPilot não foi inspecionado. Reserva provisória expirada é excluída dos agregados de gestão. |
| **UI:** Acelerar apresenta missões de cadastro, página pública, divulgação, lembretes, retorno, adicionais e avaliações; parte exige resultados de envio/agendamento. | Três marcos factuais: primeiro agendamento confirmado, serviço e expediente configurados, cinco clientes ativos com WhatsApp. A primeira confirmação considera histórico, mesmo fora do período consultado. | **Adaptado/parcial.** Não reproduz todas as missões nem atribuição de conversão. A referência aceita telefone **ou e-mail** na missão de clientes; o marco Orion conta WhatsApp. Configuração da agenda não significa site publicado ou vaga futura garantida. |
| **Estático:** preferência de visualização da agenda e estado da barra lateral podem persistir no navegador. | Estado de navegação e filtros é mantido pela suíte, com invalidação de respostas antigas quando muda a empresa/conta; o tema usa a preferência existente do painel. | **Parcial.** Não se declara reprodução de todas as preferências persistentes, transições, animações ou parâmetros de URL da referência. |

## 3. Agenda, disponibilidade e conclusão

Fontes: REF-AGENDA. Implementação: [API de booking][BOOKING], [motor existente][MOTOR], [gestão][GESTAO], [bloqueios recorrentes][BLOCOS]. Testes: [booking][T-BOOKING], [gestão][T-GESTAO], [extras][T-EXTRAS].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** lista, calendário, visão por profissional, faixa de dias, Hoje, filtros e criação em um espaço da agenda. | Consulta por dia e empresa; visão em lista/calendário/profissional; cores da equipe; desenho de expediente, pausas e bloqueios; escolha de horário consulta disponibilidade real. | **Integrado/parcial.** Reagendamento é feito por formulário, sem arrastar o compromisso. A listagem herdada da API retorna até 200 agendamentos por dia; não há paginação desse endpoint. |
| **UI + Estático:** modo tablet/imersivo, relógio e tela cheia; o bundle declara atualização periódica dos dados. | Modo tablet com agenda ampliada, controles de data e relógio atualizado. | **Parcial.** Não equivale a sincronização em tempo real. A suíte não reproduz a atualização periódica de compromissos a cada 60 segundos nem a chamada nativa de tela cheia declaradas na referência. |
| **UI:** novo agendamento com cliente, serviço, profissional e horário; **Estático:** busca de cliente/serviço e adicionais opcionais. | Cliente pode ser criado no fluxo. Um cliente existente sem telefone recebe o número informado em seu próprio cadastro antes da reserva. A Agenda aceita adicionais de um serviço base ou combo, resolvendo a seleção no servidor antes da consulta de horários. Disponibilidade considera duração, preparação, intervalo, expediente, pausas e bloqueios. A reserva usa o motor `hold → confirm` e gera protocolo. | **Adaptado/parcial.** A reserva autenticada exige WhatsApp brasileiro, embora o cadastro manual de cliente aceite somente nome. Adicionais não podem acompanhar múltiplos serviços base escolhidos separadamente. Não se deduz disponibilidade pela cor de uma célula da grade. |
| **Estático:** confirmar/concluir, falta, cancelamento, reagendamento e reabertura. | Confirmação, cancelamento e reagendamento usam o core. Conclusão e falta usam operações de gestão auditadas; exigem `confirmed` e início já ocorrido. Repetir a mesma conclusão não duplica comissão, pontos ou cupom. | **Integrado, com reabertura ausente.** Não há retorno de um atendimento concluído para agendado nem reversão de seus efeitos financeiros/fidelidade. Não é permitido concluir um agendamento futuro. |
| **Estático:** conclusão com extras de produto/serviço, nome e valor. | Conclusão pode aplicar um cupom válido e registra preço original, desconto, total líquido e comissão calculada no servidor. | **Parcial.** Extras avulsos inseridos no ato da conclusão não estão implementados. O catálogo comercial de produtos do Orion não equivale a extras no atendimento. |
| **UI + Estático:** bloqueio por horas ou dia inteiro/férias; motivo; repetição diária, semanal ou por dias específicos; exclusão de um dia, deste em diante ou da série. | Séries persistentes, expansão no fuso local, controle de conflito atômico e operações de exclusão por alcance. Um conflito invalida toda a criação; acesso genérico não modifica ocorrências protegidas de série. | **Integrado/adaptado.** Períodos limitados a até 366 dias. Horários locais inválidos/ambíguos são rejeitados. A tela e API têm validações próprias; não se reproduz uma regra interna do BarberPilot não observada. |
| **UI no site + Estático na agenda:** pedido de encaixe, aceitar/negar e escolher horário; o texto anuncia possível sobreposição. | O motor Orion mantém exclusão de intervalos ocupados e revalidação na confirmação. | **Ausente.** Não há fila pública de encaixe nem exceção autorizada para sobrepor atendimentos. |

## 4. Clientes

Fontes: REF-CLIENTES. Implementação: [schemas][SCHEMAS], [gestão][GESTAO], [dados][DADOS], [interface][UI-ORION]. Testes: [gestão][T-GESTAO] e [validações][T-UNIDADE].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** somente nome obrigatório; WhatsApp, Instagram, e-mail, aniversário, bairro, cidade e observações, com dados adicionais recolhíveis. | Campos persistentes; telefone pode ser nulo, sem número fictício. Normalização e limites validados; busca e paginação; criação idempotente e edição com versão esperada. | **Integrado/adaptado.** Limites de tamanho e validações são os do Orion, não cópias exatas dos limites do formulário BarberPilot. |
| **UI + Estático:** ativos, inativos, risco de 30/60/90 dias e aniversariantes. | Risco exclui inativos, excluídos, quem nunca concluiu atendimento e quem tem agendamento futuro válido. Aniversariantes são calculados pela semana iniciada no domingo. | **Integrado.** Busca suporta nome, telefone e e-mail. As datas e a divisão dos dias respeitam o fuso da empresa. |
| **UI + Estático:** editar, inativar, excluir, reativar e acessar contatos; o bundle descreve detalhes de histórico, gasto, ticket, faltas e fidelidade. | Edição, ativação/inativação e exclusão lógica preservam registros anteriores. Ficha mostra resumo de visitas, gastos e pontos. Exportação CSV neutraliza fórmulas e preserva campos vazios. | **Parcial.** Falta a lista detalhada de todo o histórico na ficha e equivalência integral dos gestos de swipe. Não se declara reativação automática ao novo agendamento. Exportação tem limite de 20 mil linhas. |
| **Estático:** exclusão/inativação têm efeitos apresentados em confirmações distintas. | Cliente excluído sai do diretório e das buscas, mas vínculos e histórico permanecem no banco. | **Adaptado.** Exclusão lógica não equivale a apagar todos os dados de uma pessoa nem a um fluxo completo de eliminação de conta. |

## 5. Serviços, combos e adicionais

Fontes: REF-SERVICOS. Implementação: [catálogo][CATALOGO], [schemas de catálogo][CAT-SCHEMAS], [combos/adicionais][PACOTES], [interface][UI-ORION]. Testes: [catálogo público][T-PUBLICO].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** modelos por categoria, criar/editar, nome, categoria, duração, preço, cor, imagem, profissionais e ativo. | Cadastro persistente com categoria, cor hexadecimal, URL de imagem, ordem, preço em centavos e duração. Preparação/intervalo próprios do motor também são editáveis. Seleção vazia de profissionais materializa vínculo com todos os cadastrados no momento. | **Integrado/adaptado.** Modelos são sugestões de cadastro, não dados importados da conta de referência. Imagens usam URL HTTPS; não há upload/corte de imagem. Novos profissionais não herdam automaticamente todos os serviços só porque a seleção esteve vazia anteriormente. |
| **UI + Estático:** arraste ou setas para ordenar a vitrine. | Ordem de serviços persistida por setas. | **Parcial.** Sem arraste da lista; a equivalência do gesto móvel não está implementada. |
| **UI + Estático:** combo de pelo menos dois serviços, preço especial, nome, visibilidade, destaque, foto, descrição e ordem de execução. | Combos de 2 a 6 serviços base distintos; preço validado; duração e elegibilidade derivadas dos componentes. Só profissionais aptos a todos os componentes são vinculados. O motor reserva um serviço sintético; a composição do atendimento é preservada em snapshot. | **Integrado/parcial.** A API respeita a ordem de `service_ids`, mas a tela não oferece reordenação dedicada dos componentes. Não há upload de foto. O preço especial não pode superar a soma dos componentes. |
| **Estático:** adicionais sugeridos com opção de somar duração. | Até cinco adicionais por serviço, sempre somando o preço; duração opcional. A seleção pública ou autenticada produz uma variante privada, validada contra o catálogo; variantes não aparecem como serviços independentes nem podem ser editadas pelo CRUD genérico. | **Integrado no editor, na Agenda e no agendamento público.** A seleção parte de um serviço base ou combo por vez. Não equivale a extras livres inseridos na conclusão. |

## 6. Funcionários, convites e permissões

Fontes: REF-EQUIPE. Implementação: [catálogo][CATALOGO], [equipe][EQUIPE], [convite público][CONVITE], [acesso a empresas][ACESSO]. Testes: [extras][T-EXTRAS], [catálogo público][T-PUBLICO] e [booking][T-BOOKING].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** foto, nome, especialidade, cor, comissão, recebe agendamento, ativo e serviços realizados. | Campos persistentes, vínculos de serviços e horários por profissional. `accepts_appointments=false` impede novas disponibilidades e é revalidado ao confirmar a reserva. Vínculos sintéticos de combos são preservados na edição. | **Integrado/adaptado.** Foto por URL; cadastro do profissional e acesso da conta são operações distintas. Um apelido/categoria não concede autoridade de API. |
| **UI:** convites por e-mail ou link, pendente/expirado, conta vinculada, aprovar/revogar; o formulário anuncia envio de e-mail. | Link de convite para Editor/Leitor; token não é armazenado em claro; prazo, vínculo de e-mail quando informado, aceite por conta verificada e aprovação explícita do owner. Revisões concorrentes não sobrescrevem o papel de uma associação recém-criada. | **Adaptado.** Criar o convite não envia e-mail; não há envio/reenvio automático de convite. O fluxo não vincula silenciosamente a pessoa apenas porque o e-mail coincide. |
| **UI:** padrões por categoria e ajustes individuais em **13 módulos**; dono com acesso total. | Papéis reais `owner`, `editor` e `viewer`, resolvidos pela associação persistida à empresa. Publicação do site e gestão de acessos ficam com owner; certas operações de infraestrutura continuam restritas à administração Orion. | **Parcial.** Não implementa a matriz de 13 permissões por módulo, herança de categorias ou exceções individuais do BarberPilot. Leitor/Editor não equivalem a Barbeiro/Gerente/Financeiro. Não há garantia de visão limitada ao próprio profissional dentro de uma empresa. |

## 7. Financeiro, DRE e quitação

Fontes: REF-FINANCEIRO. Implementação: [gestão][GESTAO], [agregados][DADOS], [quitações][QUITACOES]. Testes: [gestão][T-GESTAO], especialmente concorrência de lançamentos, DRE, quitação repetida e vale parcial.

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** seis indicadores, navegação mensal e abas Lançamentos, DRE, Comissões, Vales & Pagamentos e Histórico. | Receitas, despesas, saldo, margem, ticket e contagem; período e filtros; diário e categorias agregados sobre toda a consulta, não sobre uma página de resultados. | **Integrado/adaptado.** Indicadores de caixa vêm do livro financeiro. Ticket usa atendimentos concluídos. Essa mistura de bases é explicitada no código; não se assume que a fórmula privada do BarberPilot seja idêntica. |
| **UI:** lançamento de receita/despesa, categoria, valor, data e descrição. | CRUD com situação pago/pendente/cancelado, forma de pagamento e observações; valores inteiros em centavos, datas com fuso e limite; idempotência na criação, versão na alteração e cancelamento auditado. CSV com filtros. | **Integrado.** Cancelar preserva o registro. Não há sincronização bancária nem transação de dinheiro executada por esse cadastro. |
| **UI + Estático:** DRE com receita, comissões, despesas, resultado e impressão/PDF. | Receita dos atendimentos concluídos pelo valor efetivo após desconto, mais receitas manuais não vinculadas; dedução da comissão gerada e despesas operacionais pagas. Vales/pagamentos não descontam a comissão duas vezes. Impressão usa o navegador. | **Adaptado.** Concluir atendimento não cria automaticamente uma receita duplicada no livro de caixa. DRE e livro de lançamentos têm origens distintas. A impressão não é um serviço de geração fiscal/contábil. |
| **Estático:** comissões realizadas, agenda restante, média diária e previsão de fim do mês. | Comissão de concluídos com percentual/valor congelados no atendimento; agregados por profissional, valores futuros confirmados e saldo acumulado. | **Parcial.** A tela prioriza resultados registrados e quitação; não reproduz integralmente todos os gráficos e projeções de fim do mês. Atendimentos legados sem snapshot usam a taxa atual como **estimativa identificada**. |
| **UI:** vale com valor/data/descrição e opção de não registrar ainda saída do caixa. | Vale vinculado a profissional, registrado como despesa paga; classificação de compensação independente: em aberto, parcial ou quitado. | **Adaptado/parcial.** A tela atual registra saída no caixa; não oferece o checkbox da referência para vale sem saída de dinheiro. `paid` indica dinheiro adiantado, não vale já compensado. |
| **UI + Estático:** conferir comissão bruta, vales pendentes e valor a pagar; quitar e manter histórico. | Prévia do saldo acumulado com hash, revalidação na gravação, compensação FIFO de vales, possibilidade de compensação parcial e criação atômica do pagamento restante. Uma repetição idempotente não cria outra quitação. | **Integrado/adaptado.** O período visual não limita o saldo acumulado a quitar. Vales superiores ao saldo podem ser compensados parcialmente, conservando o restante; isso difere do bloqueio anunciado na referência. Nenhuma transferência externa é feita. |
| **UI:** histórico de pagamentos, vales quitados e lançamentos. | Listas paginadas, registro imutável da quitação e suas alocações. Lançamento ligado a uma quitação não pode ser editado/cancelado pelo cadastro comum. | **Integrado, sem estorno/reabertura.** Não há fluxo de reversão de quitação, realocação manual ou alteração retroativa do atendimento concluído. |

## 8. Meu desempenho e metas

Fontes: REF-FINANCEIRO. Implementação: [agregados de desempenho/metas][DADOS] e [interface][UI-ORION]. Testes: [gestão][T-GESTAO].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** Hoje, 7 dias, mês, 30/90 dias; atendimentos, vendas, comissão, vales; evolução diária/semanal e detalhamento. | Períodos e gráficos alimentados por agregados diários de receita e comissão, sem calcular comissão por média de percentuais. Resumos por profissional e saldo acumulado usam dados persistidos. | **Integrado/adaptado.** A visão é da empresa autorizada; não implementa a permissão específica de cada funcionário da referência. Vales do período e saldo acumulado têm rótulos diferentes para evitar equivalência indevida. |
| **UI/declarações de produto:** acompanhamento de evolução e resultados. | Metas persistentes por profissional, em quantidade de atendimentos ou receita, com período e andamento calculado. | **Adaptado.** O cadastro de metas é uma função Orion; não comprova identidade com todo o sistema de metas/gamificação do BarberPilot. |

## 9. Fidelização

Fontes: REF-FIDELIDADE. Implementação: [regras e endpoints][GESTAO], [livro de pontos e cupons][FIDELIDADE]. Testes: [gestão][T-GESTAO] e [schemas][T-UNIDADE].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** Ranking, Perto do prêmio, Cupons, Divulgar, Ideias IA e Configurar; contadores de pontos/cupons. | Abas conectadas, ranking paginado e proximidade a partir de 70% da meta; saldo e histórico persistentes. | **Integrado/adaptado.** Contadores têm definições próprias do livro de pontos; proximidade não é previsão de nova visita. |
| **UI:** pontos por gasto e por atendimento, meta, ativação e cupom percentual/fixo/serviço grátis com validade. | Uma regra ativa por empresa; pontos cumulativos calculados ao concluir, sob bloqueio do cliente; emissão atômica de até **um cupom automático por atendimento**, com saldo excedente preservado. | **Integrado com limite explícito.** Pontos por gasto seguem passos inteiros configurados em centavos. Não se presume mesma política de arredondamento ou de múltiplos cupons da referência. |
| **UI + Estático:** cupom automático e resgate. | Cupom com snapshot do prêmio, validade, cliente e status; resgate somente na conclusão, um por atendimento, validado pelo servidor. Percentual e valor fixo incidem sobre o total; concorrência não consome duas vezes. | **Integrado/parcial.** Serviço grátis exige `appointment_items.service_id` correspondente. Um serviço base contido em combo/variante não corresponde diretamente: retorna **422 sem consumir o cupom**. Não há reversão de cupom após conclusão. |
| **UI:** divulgação por link/mensagem e compartilhamento; ideias com IA. | Texto original para copiar, link publicado do Orion, ideias com a API de IA quando habilitada; ajustes manuais de pontos registram motivo e impedem saldo negativo. | **Adaptado.** Copiar uma mensagem não a envia. Não há envio automático de recompensa nem comprovação de entrega pelo WhatsApp nessa função. |

## 10. Marketing IA e Consultor IA

Fontes: REF-MARKETING. Implementação: [IA de gestão][IA], [preferências/automações/fila][PREFERENCIAS], [rascunhos de campanha][GESTAO], [recursos avançados existentes][AVANCADOS]. Testes: [extras][T-EXTRAS] e [IA/validações][T-EXTRAS-UNIDADE].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** briefing, tom, textos de campanha para diferentes canais e gerações salvas. | Geração estruturada de nome, oferta, WhatsApp, Instagram, Stories e notificação; histórico e fontes; salvar/editar/arquivar rascunhos por empresa. | **Integrado condicionado ao provedor.** `MANAGEMENT_AI_ENABLED` e configuração do provedor controlam disponibilidade. Testes controlados validam contrato, não campanha real paga. Arquivar não executa envio. |
| **UI:** confirmações, lembretes, aniversário, pós-atendimento e fidelidade, com chave de ativação e modelos. | Preferências versionadas para 11 eventos, texto, intenção de ativação, prazo de recuperação e preenchimento de vaga. Retorno da API declara `mode=draft` e `execution_enabled=false`. | **Rascunho.** Não agenda nem dispara essas novas rotinas. O status de um canal conectado não equivale a automação de marketing funcionando. |
| **UI:** recuperação em etapas, análise de clientes, Autopilot, candidato para vaga cancelada e métricas de conversão/receita recuperada. | Segmentos de clientes em risco e configuração de recuperação/vagas, com mensagens preparadas para revisão. | **Parcial/rascunho.** Não há seleção automática de candidato, execução de régua, convite rastreado, simulação equivalente ou atribuição de receita recuperada. Não se apresentam números de conversão inventados. |
| **UI:** fila de pendentes/enviadas/canceladas/todas. | Fila **real de respostas do canal Meta** (`message_outbox`), com pendentes/enviadas/com falha, tentativas e próxima tentativa; identificação do destinatário reduzida ao sufixo. | **Adaptado.** Não é fila das campanhas salvas, nem o histórico completo do conector QR. Não implementa todos os estados/controles de cancelamento da fila BarberPilot. |
| **UI:** consultor conversacional e ações sugeridas. | Consultor recebe agregados limitados do negócio; retorna resposta, fontes e ações restritas às rotas de gestão. Não executa ferramentas nem mutações indicadas pelo modelo. Pedido idempotente evita custo duplicado da mesma operação; o acesso é revalidado após a resposta. | **Integrado condicionado ao provedor.** Histórico/contexto são limitados. Não há comprovação de qualidade de toda resposta nem equivalência ao modelo ou aos prompts do BarberPilot. |
| **UI:** saldo, recarga e custos em créditos/tokens do produto. | Uso real retornado pelo provedor quando disponível, histórico de gerações e limite diário por empresa com renovação em UTC. | **Adaptado/parcial.** Não há moeda comercial de créditos, compra/recarga ou equivalência de cotas/preços aos planos BarberPilot. |

## 11. Meu Site e agendamento público

Fontes: REF-SITE e REF-PUBLICO. Implementação: [preferências][SCHEMAS], [API pública][PUBLICO], [página pública][PAGINA-PUBLICA], [combos/adicionais][PACOTES]. Testes: [público e catálogo][T-PUBLICO] e [assinatura/limites][T-PUBLICO-UNIDADE].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** nome, contatos, slug, logo/capa/cor, descrição, endereço/mapa, cidade/UF/CEP, redes sociais e rodapé. | Campos persistentes, URLs HTTPS e telefones validados; rascunho com versão; mostrar preços e ocultar marca Orion. A página pública usa `/s/{slug}`. | **Integrado/adaptado.** Imagens por URL, sem upload/crop. O formulário salva explicitamente; não reproduz todo autosave da referência. Marca e endereços são do Orion. |
| **UI:** prévia, copiar/abrir endereço e publicação da página. | Prévia autenticada do rascunho; publicação/republicação/retirada pelo owner; snapshot dos campos públicos aprovados e slug público separado do rascunho. | **Integrado/adaptado.** Um editor pode preparar o rascunho, mas não publicar. O snapshot cobre a apresentação; **catálogo, preços, profissionais e disponibilidade continuam vivos**, revalidados em cada cotação/reserva. A publicação exige configuração operacional do agendamento público. |
| **UI:** acessos, clientes que agendaram, agendamentos e conversão, com evolução diária. | Métricas persistentes de abertura e agendamento confirmado pela página pública. Prévia e agendamento criado pela equipe não entram como reserva pública. | **Parcial/adaptado.** Contagem de acessos não é visitantes únicos nem analytics equivalente. Não há uma métrica distinta de clientes únicos que agendaram. |
| **UI:** vitrine de serviços, equipe, mapa/contatos; fluxo de serviço → profissional/dia/horário → confirmação. | Página responsiva com serviços/combos/adicionais, profissionais elegíveis, agenda real, nome/WhatsApp e confirmação explícita. Cotação assinada, expiração, limites Redis e replay idempotente; preço não é aceito como autoridade do navegador. | **Integrado/adaptado.** A confirmação final da referência não foi submetida. Orion não oferece conta/autenticação pública do consumidor, diretório de outras empresas ou todos os controles pós-agendamento da referência. |
| **UI:** serviços destacados e banners promocionais com imagem, link, desconto, ordem, ativo e carrossel. | Combos podem ter destaque e serviços têm ordem persistente. | **Parcial.** Não há sistema de banners/carrossel nem destaque individual equivalente para todos os serviços. |
| **UI:** domínio próprio e ocultação de marca condicionados a plano; seção de avaliações e pedido de encaixe. | Ocultação de marca é uma preferência pública do Orion, sem copiar o bloqueio comercial do BarberPilot. | **Parcial/ausente.** Sem domínio personalizado, avaliações públicas, estrelas calculadas, fila de espera/encaixe ou aviso externo ao aceitar encaixe. |

## 12. Configurações, notificações e perfil

Fontes: REF-CONFIG. Implementação: [preferências][PREFERENCIAS], [interface][UI-ORION], [contas][CONTAS], [painel original][PAINEL]. Testes: [extras][T-EXTRAS] e [schemas][T-EXTRAS-UNIDADE].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** dados operacionais/fiscais, telefone administrativo, meta, CPF/CNPJ, nomes empresariais. | Preferências privadas com versão e acesso restrito de gravação; documento é omitido para viewer. Campos fiscais não são publicados pelo site. | **Integrado/adaptado.** Validação do documento é de formato/comprimento, não consulta cadastral nem emissão fiscal. Preferências do negócio e apresentação pública são separadas. |
| **UI:** escolher profissional, expediente semanal, total de horas e editar em massa. | Expediente por profissional/dia, pausas, resumo semanal e edição de períodos. O motor considera essas informações. | **Parcial.** Sem edição em massa/copiar semana; o schema atual admite um expediente por profissional por dia, com pausas internas. |
| **UI:** pedido automático de avaliação Google e resumo semanal por WhatsApp. | Link, texto e intenção de ativação persistidos com indicação de rascunho. | **Rascunho.** Concluir um atendimento não dispara esses convites/relatórios novos. Não há busca de avaliações públicas. |
| **UI:** sino, Todas/Não lidas, Ler todas; push no aparelho e preferências por evento. | Sino baseado no histórico real de agendamentos, com leitura individual por conta. | **Parcial.** Sem registro de push, service worker de notificações ou eventos equivalentes de avaliação, metas, assinatura e novidades. O sino não é prova de entrega no celular. |
| **UI:** nome/e-mail/senha editáveis, confirmação de novo e-mail, exclusão da conta e push. | Consulta da conta no painel; login, verificação de e-mail e recuperação de senha permanecem no fluxo existente do Orion. | **Parcial.** Não há editor completo de perfil, troca autenticada de e-mail/senha nesta nova suíte, exclusão definitiva da conta ou push. Recuperação de senha não equivale ao formulário de alteração visto na referência. |
| **Código Orion preservado:** Google Agenda. | Consulta da conexão existente e comandos sujeitos às permissões reais do módulo. | **Adaptado ao Orion.** Conectar pode exigir administração. Não foi criada uma autorização fictícia para que todo editor opere a integração. Serviços/credenciais externos exigem validação operacional separada. |

## 13. Plano, base de conhecimento, novidades e suporte

Fontes: REF-CONTEUDO. Implementação: [assinatura consultável][GESTAO], [interface][UI-ORION] e [contas][CONTAS].

| Função/estado da referência | Implementação real no Orion | Situação e limite |
|---|---|---|
| **UI:** plano em teste, preços mensal/anual, recursos, contratar e administrar assinatura via provedor. | Consulta da assinatura e planos reais do banco, com mensalidade, limites e datas disponíveis. Membro consulta somente sua empresa, sem acessar a listagem administrativa de todas as assinaturas. | **Parcial.** Sem checkout, contratação/troca/cancelamento de plano na nova tela, anualidade equivalente ou tabela de preços copiada. A API declara `checkout_enabled=false`. |
| **UI:** artigos, categorias, busca, leitura, conteúdos relacionados e contato com suporte. | Base de ajuda com conteúdo original do Orion e atalhos para as funções implementadas. | **Adaptado.** Não reproduz o catálogo integral de artigos BarberPilot nem seus textos. Não declara disponibilidade de uma equipe ou prazo de suporte da referência. |
| **UI:** novidades por tipo/data, avisos push e retomada de tour. | Conteúdo próprio sobre esta versão e navegação de ajuda. | **Parcial.** Não replica histórico editorial do BarberPilot, notificações push de novidades nem um tour guiado equivalente em todos os módulos. |
| **UI/declaração:** rótulos de Loja de Recursos e Clube de Assinaturas aparecem em permissões/notificações. | Não foram criados módulos equivalentes a partir desses rótulos isolados. | **Ausente/sem comprovação de fluxo completo na referência.** Um nome de evento ou permissão não é evidência suficiente para afirmar que o recurso foi integralmente inspecionado. |

## 14. Funções do Orion preservadas

O menu mantém acesso ao conector WhatsApp, conversas, testes de texto/áudio, configuração/publicação do atendimento, catálogo comercial/pedidos e cadastros avançados. Esses módulos usam os fluxos e permissões existentes do Orion. Serviços de agenda e produtos comerciais permanecem entidades diferentes. A nova interface não declara uma conexão ativa apenas por exibir o menu e não publica automaticamente o atendimento ao salvar Meu Site. Fontes: [painel][PAINEL], [perfil do negócio][PERFIL-NEGOCIO], [API QR][QR] e [API comercial][COMERCIAL].

Nenhuma captura da referência foi usada como cadastro de clientes/profissionais do Orion. A disponibilidade em produção de WhatsApp, áudio, IA, Google e agendamento público deve ser verificada no ambiente correspondente; testes locais com transporte controlado não substituem essa verificação.

## 15. Lacunas que impedem declarar paridade integral

1. **Conclusão e reversão:** extras avulsos de produto/serviço, reabertura de atendimento e reversão coordenada de caixa, comissão, pontos e cupom.
2. **Agenda:** encaixe com sobreposição, fila de espera pública, arraste para reagendar, atualização automática equivalente do modo tablet e paginação acima de 200 compromissos/dia.
3. **Acesso da equipe:** matriz de 13 módulos, padrões por categoria, exceções individuais e visão pessoal vinculada ao profissional; envio/reenvio automático de convite por e-mail.
4. **Marketing operacional:** execução de novas automações, réguas de recuperação, Autopilot, seleção automática de candidato, links rastreados, métricas de entrega/clique e atribuição de receita recuperada.
5. **Site público:** upload/crop, domínio próprio, banners/carrossel, avaliações, fila de encaixe e conta pública do consumidor.
6. **Fidelidade:** prêmio de serviço base dentro de combo/variante, reversão de resgate e envio automático de recompensa. O limite de um cupom automático por atendimento e o saldo excedente são regras explícitas desta versão.
7. **Financeiro:** vale sem saída de caixa no formulário, estorno de quitação, edição retroativa e equivalência de todos os gráficos/projeções. Pagamentos registrados não transferem dinheiro.
8. **Cadastros e operação:** histórico detalhado na ficha do cliente, arraste de serviços, reordenação dedicada do combo e edição de expediente em massa.
9. **Conta e plataforma:** editor completo de perfil, exclusão de conta, push, checkout/planos anuais, compra de créditos, tour e catálogo editorial equivalentes.
10. **Verificação integral:** não há comprovação de cada estado preenchido, erro, gesto, combinação de permissão, viewport físico e integração externa do BarberPilot. A reconstrução visual deve ser avaliada separadamente da correção transacional do Orion.

Essas lacunas são limites identificados da entrega, não ações já executadas nem compromissos implícitos de ativar serviços externos.

## 16. Rastreabilidade dos testes do Orion

| Área | Exemplos de cenários existentes | Fonte |
|---|---|---|
| Empresas e clientes | Autorização antes do acesso ao banco; associação revogada; RLS sem contexto; vínculo cruzado; telefone opcional; conflito de versão; CSV seguro. | [Gestão][T-GESTAO], [unitários][T-UNIDADE] |
| Onboarding | Empresa vizinha preenchida não conclui missões; hold não conta; confirmação histórica continua fora do período; telefone/atividade/configuração reais. | `test_onboarding_uses_lifetime_scoped_facts_and_current_configuration` em [gestão][T-GESTAO] |
| Conclusão e fidelidade | Conclusão futura rejeitada; replay sem duplicar; snapshot de comissão; saldo não negativo; emissão de um cupom; cupom expirado/de outro cliente; consumo concorrente único. | [Gestão][T-GESTAO] |
| Financeiro e quitação | DRE sem dupla contagem; concorrência de lançamento; hash da prévia obsoleta; quitação repetida; vale parcialmente compensado; histórico imutável. | [Gestão][T-GESTAO] |
| Recorrência, equipe e preferências | Série atômica; conflito concorrente; horários de transição de fuso; convite aceito e aprovado; duas aprovações não sobrescrevem membro; rascunho não enfileira envio; leitura de notificação por conta. | [Extras][T-EXTRAS], [unitários][T-EXTRAS-UNIDADE] |
| IA | Saída estruturada e limitada; falha de provedor; reserva evita custo duplicado; associação revalidada após resposta; ausência de saldo comercial inventado. | [Extras][T-EXTRAS], [unitários][T-EXTRAS-UNIDADE] |
| Público e catálogo | Interseção de profissionais do combo; duração opcional de adicional; variante privada; publicação owner/snapshot; cotação expirada; consentimento; replay/conflito; composição imutável; Redis real; RLS. | [Integração pública][T-PUBLICO], [unitários][T-PUBLICO-UNIDADE] |
| Interface | Montagem da suíte, estados e contratos com ponte de requests controlada. | [Suíte UI][T-UI], [instruções de UI][UI-TEST-README] |

O último resultado global, eventuais skips e restrições de ambiente devem ser lidos no [documento principal](barberpilot-orion.md) e no [runbook](management-suite-runbook.md). Esta lista descreve cenários existentes e evidência por função; não transforma um teste de API em aprovação visual ou confirmação de funcionamento de um provedor real.

[UI-ORION]: ../app/static/orion-suite.js
[CSS-ORION]: ../app/static/orion-suite.css
[DADOS]: ../app/management_models.py
[GESTAO]: ../app/management_api.py
[SCHEMAS]: ../app/management_schemas.py
[BOOKING]: ../app/booking_api.py
[MOTOR]: ../app/booking_service.py
[BLOCOS]: ../app/management_blocks.py
[CATALOGO]: ../app/catalog_api.py
[CAT-SCHEMAS]: ../app/catalog_schemas.py
[PACOTES]: ../app/service_packages_api.py
[EQUIPE]: ../app/management_team.py
[CONVITE]: ../app/static/orion-invite.js
[ACESSO]: ../app/tenant_access.py
[QUITACOES]: ../app/financial_settlements.py
[FIDELIDADE]: ../app/loyalty_rewards.py
[IA]: ../app/management_ai.py
[PREFERENCIAS]: ../app/management_preferences.py
[AVANCADOS]: ../app/advanced_api.py
[PUBLICO]: ../app/public_site_api.py
[PAGINA-PUBLICA]: ../app/static/orion-public.js
[CONTAS]: ../app/account_auth.py
[PAINEL]: ../app/static/panel.html
[PERFIL-NEGOCIO]: ../app/business_profile_api.py
[QR]: ../app/qr_api.py
[COMERCIAL]: ../app/commercial_api.py
[T-GESTAO]: ../tests/integration/test_management.py
[T-UNIDADE]: ../tests/unit/test_management_unit.py
[T-BOOKING]: ../tests/integration/test_booking.py
[T-EXTRAS]: ../tests/integration/test_management_extras.py
[T-EXTRAS-UNIDADE]: ../tests/unit/test_management_extras_unit.py
[T-PUBLICO]: ../tests/integration/test_public_catalog.py
[T-PUBLICO-UNIDADE]: ../tests/unit/test_public_site.py
[T-UI]: ../ui-tests/orion-suite.test.mjs
[UI-TEST-README]: ../ui-tests/README.md
