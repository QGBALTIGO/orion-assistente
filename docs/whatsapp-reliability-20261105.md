# Confiabilidade do WhatsApp do Orion

O escopo desta alteração é o transporte WhatsApp e suas dependências diretas. A publicação e a observação de produção serão registradas na seção de validação. Reconexão, retorno de envio e recibo de leitura são evidências diferentes; nenhum deles, isoladamente, comprova a legibilidade de todos os formatos.

## Arquitetura e responsabilidade pela sessão

O Railway mantém API, worker, bridge Baileys, PostgreSQL e Redis separados. A bridge tem uma réplica e volume persistente em `/data`. API e worker não abrem sockets Baileys. O pacote efetivamente executado no diagnóstico inicial foi Baileys **6.7.24**, em Node **22.23.4**; a versão Web negociada é um parâmetro separado.

| Fluxo | Entrada e autorização | Processamento e saída | Persistência e confirmação |
| --- | --- | --- | --- |
| Barbearia e canal gerenciado | `channel-session` ou sessão principal; empresa, canal, credencial, responsável e fence verificados no servidor | Revisão de entrada, arbitragem com humano, `qr_ai.run_locked`, regras e ferramentas; fila por conversa e envio autorizado | Estado da conversa no Redis; operações e histórico no PostgreSQL; conteúdo de retry cifrado antes do relay; recibos separados de aceitação |
| Serviços de assinaturas | Evento vinculado a dispositivo, empresa, geração e revisão; evento persistido antes de processamento | Jobs e regras por contato; efeitos ordenados, limites, opt-out e autorização final; bridge consulta efeitos pendentes | Eventos, efeitos, seleções e histórico no PostgreSQL; ledgers e protocolo cifrados no volume; resultado incerto não vira um novo envio |
| Sessão principal preservada | Com `QR_LEGACY_CENTRAL_ENABLED=1`, identidade preparada no registro SQL; a bridge adquire e renova a mesma lease utilizada pelo adaptador gerenciado | A reserva ocorre antes de ler/escrever o estado Signal ou construir o socket; perda de responsabilidade encerra o socket antigo | A sessão original permanece no mesmo diretório. Não há cópia de credenciais para abrir outro socket |

O nicho escolhe regras e conteúdo; não deve escolher outra identidade criptográfica. Não foi demonstrado que trocar o nicho seja, por si só, a causa dos erros de criptografia.

## Causas reproduzidas e correções

| Problema comprovado em código ou teste | Cenário anterior | Comportamento corrigido |
| --- | --- | --- |
| Resposta atrasada depois da expiração da trava da conversa | Uma inferência suspensa por 91 segundos ainda passava na guarda de escrita após outro responsável assumir a conversa | Prazo da requisição, renovação e comparação do token; verificação antes das escritas e na saída das transações; resultado tardio não autoriza nova escrita |
| Recuperação sobrescrevendo o estado do responsável seguinte | Verificar a posse e restaurar o estado eram comandos Redis separados | Comparação de token e restauração/ajuste do estado na mesma operação Lua |
| Lote com uma mensagem repetida descartava também mensagens novas | `any(done)` encerrava todo o lote | Partes autenticadas vinculam ID e texto; somente as partes novas são processadas. Cliente antigo recebe esclarecimento, sem divisão ambígua por quebras de linha |
| Socket principal fora do controle distribuído de responsabilidade | O adaptador protegia envios de aplicação, mas a sessão e as tentativas internas do SDK já podiam estar abertas | Lease SQL antes do socket, backup e autenticação; guardas no repositório Signal e armazenamento; perda da lease invalida geração e encerra transporte |
| Revogação liberava uma sessão antes de o processo antigo observar a mudança | Alterar a configuração zerava a concessão; outro processo podia assumir as mesmas credenciais durante o prazo local ainda válido | Autorização revogada imediatamente; reserva interna bloqueia qualquer novo responsável até o prazo original, sem prolongá-lo nem permitir autenticação com a identidade reservada |
| Encerramento sem coordenação completa da bridge | SIGTERM/SIGINT não tinham drenagem central que fechasse todos os sockets e tarefas | Encerramento limitado a oito segundos, fechamento de gerenciadores e timers, preservação dos arquivos e evidências de envios incertos |
| Recuperação uniforme para motivos diferentes | Logout, substituição e falha transitória podiam alimentar a mesma política | Categorias explícitas, intervalos progressivos e orçamento finito; código 500 permite duas tentativas com o estado intacto antes de revisão; abertura breve não reinicia indefinidamente o orçamento |
| Estado conectado salvo podia parecer atual após parar de reportar | Último evento de abertura era usado sem informação suficiente de prontidão | Sinais passivos de WebSocket, atividade, interrupção, recuperação, tentativa e ação; snapshot sem atualização por 90 segundos aparece indisponível |
| Verificador de prontidão do WhatsApp rejeitava schemas posteriores à migração de telefones do Paraguai | Somente a revisão exata 0052 selecionava o CHECK com +595; schemas posteriores eram comparados com a regra antiga | Seleção pela ancestralidade exata das migrations conhecidas no código; aliases, revisões parciais e desconhecidas continuam recusados; RLS, privilégios e validação do CHECK mantidos |
| Política de revisão Web divergente entre os caminhos | A sessão principal respeitava `QR_WA_VERSION_AUTO`; os dispositivos gerenciados ignoravam a flag e usavam sempre a revisão incluída no SDK | Seletor compartilhado, consulta oficial limitada a 3,5 segundos, versão obtida fixada durante o processo e fallback identificado com intervalo de 60 segundos. Biblioteca e estado criptográfico preservados |

As chaves e sessões válidas não são apagadas para corrigir falhas transitórias. O prazo de processamento da resposta ao vivo é de 14 segundos desde a entrada na rota, incluindo verificações de autoridade e espera pela trava. O preview de áudio tem orçamento próprio de 60 segundos. Limpeza e fechamento têm limites próprios; o prazo de negócio não deve ser confundido com o tempo total de finalização sob uma dependência indisponível.

O corte que rejeitava mensagens anteriores à criação do socket também descartava entradas recebidas durante uma queda curta e reapresentadas como `append` pelo SDK. A recuperação agora admite até cinco minutos nos dispositivos de assinaturas e quinze minutos nos canais gerenciados de barbearia, mantendo os IDs para deduplicação. Notificações novas conservam o corte de tempo atual, e histórico mais antigo não dispara respostas retroativas. Interrupções maiores que essas janelas continuam exigindo conferência do histórico.

## Desconexões observadas antes da publicação

No período consultado, a sessão principal registrou dez encerramentos. Horários abaixo em **America/Cuiaba**. Os logs antigos não identificavam empresa, instância e fence em cada evento; algumas aberturas e quedas receberam timestamps quase iguais no mesmo lote de logs. Por isso, não é possível atribuir uma duração precisa de recuperação a todos os episódios.

| Data e hora | Código |
| --- | --- |
| 04/11/2026 15:47:34 | 428 |
| 04/11/2026 16:45:51 | 500 |
| 04/11/2026 19:33:26 | 500 |
| 04/11/2026 20:11:24 | 503 |
| 04/11/2026 21:30:37 | 500 |
| 04/11/2026 21:47:31 | 428 |
| 05/11/2026 06:53:48 | 500 |
| 05/11/2026 10:18:53 | 500 |
| 05/11/2026 10:59:00 | 428 |
| 05/11/2026 13:02:14 | 428 |

Houve publicações da bridge no mesmo período, mas as quedas das 10:59 e 13:02 ocorreram dentro do mesmo deployment. Assim, deployment não explica todas as desconexões. Nas métricas disponíveis de 24 horas, a memória variou aproximadamente de 0,075 a 0,222 GB, com limite próximo de 1 GB; a bridge não registrou deployment falho nesse recorte. Isso não demonstra que rede, bloqueio do event loop ou um incidente mais antigo estejam excluídos.

Os seis cache misses encontrados no primeiro diagnóstico estavam todos classificados como **não rastreados como envios da aplicação**. Não são prova de que seis mensagens de clientes perderam seu conteúdo. Também havia eventos de descriptografia e destinatários não resolvidos; sem correlação com o conteúdo legível no aparelho, não se convertem esses contadores em uma taxa de falha de 90%.

Na inspeção de serviços digitais havia um dispositivo habilitado, mas parado, com concessão expirada às 12:02 de 05/11. Depois de um relink e uma preparação de conexão às 11:36, foram registrados nove estados de desconexão e nenhuma nova confirmação de abertura; a abertura anterior era de 04/11 às 20:11. O sucesso da operação de preparar a conexão não comprova autenticação. Não se atribui esse histórico à divergência de versão Web sem o motivo do protocolo e uma tentativa autenticada. A inspeção não executou logout, relink ou exclusão de credenciais.

## Limites do protocolo que continuam relevantes

O SDK 6.7.24 perde a informação de domínio em parte da extração de dispositivos PN/LID. A validação do destinatário não permite deduzir telefone a partir de LID opaco. A atualização para 7.x não foi feita automaticamente: muda contratos e armazenamento e também exige qualificação dos caminhos de retry.

Um teste com o event buffer real do SDK reproduziu `connection.update(open)` enquanto `messages.upsert` permanecia retido à espera do encerramento da sincronização inicial. A bridge passa a detectar esse estado depois de um prazo e duas observações passivas, reconectando somente o número afetado com orçamento finito. Não força `ev.flush()` nem altera o protocolo criptográfico. **Mensagens já confirmadas pelo SDK, mas retidas somente no buffer anterior ao evento de aplicação, não têm recuperação garantida por esta correção.** A ocorrência desse defeito específico na produção do Orion ainda exige correlação dos novos sinais.

A documentação upstream declara a linha abaixo de 7 sem suporte; isso é uma limitação de manutenção, não uma vulnerabilidade demonstrada por si só. O `npm audit` executado nesta alteração não encontrou avisos conhecidos nas dependências de produção.

## Compatibilidade dos formatos

Templates do Orion são conteúdo salvo e regras de substituição; não equivalem a templates HSM aprovados pela Meta.

| Formato | Contrato e limite do Orion | Verificação e alternativa |
| --- | --- | --- |
| Texto, links e variáveis | Texto de template até 32.000 unidades UTF-16; variáveis permitidas explicitamente; dados do contato e responsável selecionados no tenant | Validação de schema, substituição simples sem avaliação de código, rejeição de variável desconhecida ou fato obrigatório ausente. Legibilidade real é um teste separado |
| Imagem | JPEG até 5 MB, legenda até 1.024 unidades UTF-16 | Preparação de mídia, integridade, escopo e geração de protocolo; renderização no aparelho depende de QA real |
| Sticker | WebP até 500 kB, sem legenda | Normalização e limites; sem promessa de QA físico enquanto não registrado |
| Vídeo e vídeo circular | MP4 até 16 MB; vídeo circular sem legenda | Conversão e inspeção de mídia, limites e envio ordenado; reprodução física deve ser conferida |
| Áudio | MPEG até 16 MB; legenda e texto complementar tornam-se efeitos de texto ordenados | Não se apresenta áudio comum como nota de voz Opus validada em todos os clientes |
| Documento | PDF, DOCX, XLSX, APK e TXT até 10 MB | Validação de formato, armazenamento privado, hash e autorização por empresa; aceitação do WhatsApp não é comprovação de abertura no destinatário |
| Lista | Texto numerado por padrão, mantendo os IDs de seleção no servidor | O cliente cita a mensagem do menu e informa a opção. Mapa cifrado, limitado e com 48 horas; outro telefone/canal, número sem citação e opção expirada não selecionam uma ação |
| Carrossel | Títulos e textos em alternativa numerada | A apresentação nativa depende de opt-in. As seleções de carrossel atuais são evidência de interação; o backend não lhes atribui uma ação de negócio inexistente |
| Localização, respostas e citações | Payload validado e referências vinculadas à conversa | Não se reutiliza uma referência de outra empresa ou destinatário |

`QR_NATIVE_INTERACTIVE_ENABLED=1` conserva o caminho nativo para homologação explícita. Um envio nativo aceito nunca provoca automaticamente um segundo envio em texto apenas porque sua exibição ainda não foi comprovada.

## Pendências e prevenção de abuso

A autorização final reavalia empresa, canal, geração do dispositivo, configuração, responsável, fence, destinatário, conteúdo congelado, opt-out, finalidade, prazo e predecessor. Lembrete de agendamento passado, cancelado ou modificado perde a autorização. Atendimento humano e revisão de contato/configuração invalidam respostas antigas. Resultado de envio incerto mantém sua evidência e exige reconciliação; não retorna automaticamente a uma nova tentativa.

Variáveis de assinatura aceitas são `{name}`, `{phone_number}`, `{my_name}`, `{my_email}` e `{my_contact_number}`. Falta de um fato usado na mensagem impede renderização; placeholders desconhecidos são rejeitados. Templates de agendamento removem blocos opcionais sem dados e rejeitam fatos obrigatórios não resolvidos.

Não existe, neste módulo de assinaturas, uma entidade de fatura ou cobrança paga que permita cancelar semanticamente todo texto agendado após pagamento. Essa dependência precisa ser modelada antes de afirmar esse comportamento. Limites e opt-out existentes não representam imunidade a restrições impostas pelo WhatsApp.

## Capacidade demonstrada

O microbenchmark executou o Lua de entrada real do Redis em infraestrutura local isolada: 1.000 namespaces de empresa, dois canais por empresa, 2.000 conversas lógicas, 14.000 eventos únicos e 12.000 tentativas duplicadas, com no máximo 64 conversas de teste em execução ao mesmo tempo. A rodada levou 9,647 segundos, aproximadamente 1.451 eventos únicos por segundo, com p95 de 40,778 ms para o par de operações de deduplicação.

Esse resultado demonstra atomicidade e isolamento desse componente. Ele envolve **zero conexões WhatsApp reais** e não mede capacidade do Baileys, fila SQL, mídia, IA ou infraestrutura Railway. O gerenciador atual admite dez contextos ativos por processo por padrão; não foi elevado arbitrariamente para milhares. Volume de sessões de 256 MB e memória de 1 GB exigem dimensionamento e medição por conexão antes de ampliar esse limite.

O teto de cache de protocolo de um dispositivo pode chegar a aproximadamente 187,5 MiB, além do mapa de seleção. Teto individual não é reserva de capacidade: vários canais próximos desse limite excederiam o volume atual. Armazenamento cheio pode impedir novos envios antes do relay; acompanhar disco e retenção é requisito para ampliar a quantidade de números.

## Configurações e operação

| Configuração | Finalidade |
| --- | --- |
| `QR_SESSION_DIR` | Diretório persistente da sessão principal; manter sob `/data` |
| `QR_BRIDGE_TOKEN` | Autenticação interna e proteção do estado local; preservar em reinícios, restauração e transferência |
| `QR_CHANNEL_BROKER_URL`, `QR_BRIDGE_URL` | Origens internas fixas, sem redirecionamento ou destino fornecido por mensagem/IA |
| `QR_CHANNELS_ENABLED`, `QR_LEGACY_CENTRAL_ENABLED` | Coordenação SQL dos canais e da sessão principal preparada |
| `QR_AI_INTAKE_REVISION_ENABLED` | Controle das revisões de entrada; a guarda da trava continua necessária independentemente dessa flag |
| `INBOX_CONTROL_ENABLED`, `INBOX_SEND_ENABLED` | Arbitragem com operadores e outbox humano; não ativar globalmente para executar um teste |
| `QR_NATIVE_INTERACTIVE_ENABLED` | Opt-in de formatos nativos; padrão conservador em texto |
| `QR_WA_VERSION_AUTO` | Política compartilhada de revisão Web; correlacionar `web_version` e `web_version_source` com incidentes antes de alterá-la |

A revisão Web, o build, deployment, instância, responsável e fence entram nos diagnósticos. O painel recebe somente estado, datas, tentativa, motivo classificado e ação apropriada; não recebe chaves, tokens, conteúdo de mensagens ou identificadores de infraestrutura. O log passivo de saúde a cada 60 segundos distingue o processo vivo de uma sessão pronta.

O modo principal não gerenciado, com `QR_LEGACY_CENTRAL_ENABLED=0`, mantém compatibilidade e não oferece a exclusividade distribuída do modo gerenciado. A produção diagnosticada estava com essa flag habilitada. Não desabilitá-la como estratégia de rollback ou durante a transferência de sessão; o modo não gerenciado exige um único processo.

### Publicação e reversão

1. Conferir o SHA da produção e integrar alterações concorrentes sem sobrescrever o trabalho de outras tarefas.
2. Executar os testes no commit a publicar, incluindo Linux para fsync, permissões e symlinks. Manter falhas herdadas e testes incompletos identificados.
3. Preservar o volume e o segredo de criptografia. Não executar logout, exclusão de sessão ou novo QR como parte de um deploy normal.
4. Publicar código compatível com a transição entre versões. A capacidade `incoming_parts` da lease permite que uma bridge nova continue atendendo enquanto a API antiga ainda está em execução.
5. Acompanhar API, worker e bridge até o deployment efetivo ficar saudável; checar lease, mesma identidade e nenhuma nova autenticação desnecessária.
6. Executar envio limitado ao destinatário autorizado, reconciliar o mesmo ID e conferir leitura e legibilidade separadamente. Não reenviar uma operação incerta com ID diferente.
7. Se houver regressão, restaurar o deployment anterior do componente afetado, preservando volume e segredos. Campos novos de entrada são opcionais e a política de envio não deve reabrir operações incertas.

### Recuperação e transferência para VPS

Primeiro verificar o motivo: 401 solicita nova autenticação por logout/revogação; 403 exige revisão da restrição; 411 exige compatibilidade; 440 exige resolver disputa da sessão. Falhas transitórias usam a sessão existente e o orçamento de recuperação. Código 500 não autoriza concluir corrupção nem apagar arquivos.

Para transferir a sessão, pausar novos envios, encerrar o responsável antigo com drenagem e confirmar que ele perdeu a lease antes de iniciar o sucessor. Fazer backup cifrado do diretório inteiro de `/data`, incluindo marcadores, auth, chaves, estado de sincronização, caches de retry, mapas de seleção e ledgers; preservar o segredo separadamente. Restaurar uma cópia isolada e verificar integridade antes de ligar o socket. Nunca manter Railway e VPS ativos contra cópias da mesma sessão.

As origens atualmente aceitas são da rede privada Railway. A migração efetiva exige uma configuração explícita de rede privada/TLS para VPS, com os mesmos testes de autorização e ausência de redirecionamentos. Não basta substituir a URL por uma origem arbitrária nem publicar a porta da bridge na internet.

## Validação e observação

Estado antes da publicação: **em homologação**. As execuções abaixo têm conjuntos que se sobrepõem; seus totais não devem ser somados.

| Execução | Resultado efetivamente observado |
| --- | --- |
| Transporte Node, excluindo somente o fixture de painel já identificado como falho na base | 816 casos; 811 aprovados, zero falhas, cinco verificações POSIX/symlink não executadas no Windows. TLS/OpenSSL executado; inclui 14 casos novos da política de versão Web |
| Comparação completa de unidades WhatsApp por identidade | Candidato com 5.117 aprovados, 46 falhas, dez skips e sete erros de preparação. Todos os 44 casos adicionados aprovados; nenhuma falha nova, remoção de caso ou novo skip. A suíte global continua reprovada por falhas herdadas |
| P0 com PostgreSQL e Redis reais e dados sintéticos isolados | 41/41 aprovados, incluindo RLS, lease, expiração, perda da autoridade, Lua real e dois cenários em processos separados; repetição após a correção de quarentena com 46/46, incluindo cinco casos novos de revogação e validação |
| Recuperação de tarefas pendentes com PostgreSQL real | 9/9 aprovados; timeout/resultado incerto, autorização revogada e reconciliação sem novo envio |
| Correção do verificador de prontidão | Falha reproduzida antes; 92 testes direcionados de unidade e PostgreSQL aprovados depois, incluindo todo o módulo de prontidão WhatsApp e execução a partir de outro diretório |
| Preview de áudio | 16/16 aprovados |
| Revisão independente de regressões críticas da bridge | 104/104 aprovados |
| Painel de dispositivos e estados de conexão | 25/25 testes DOM aprovados antes do commit |
| Dependências de produção | `npm audit`: zero avisos conhecidos, sem mudança de versão ou lockfile |
| Primeira qualificação Linux no commit `580e32e` | Transporte completo com 881 casos, 862 aprovados e as mesmas 19 falhas herdadas do fixture de painel; 41 casos adicionais aprovados, zero skips/cancelamentos; Docker, auditoria, painel 25/25 e gate de 93 casos aprovados. A correção posterior da quarentena exige nova execução no novo commit |
| Execução ampla de 257 integrações PostgreSQL | Incompleta: limite local de 1.200 segundos. Não equivale a uma aprovação ampla |

Os testes de subprocesso encerram um responsável SQL sintético, demonstram rejeição do sucessor antes de expirar a concessão, nova fence depois da transferência e bloqueio do responsável antigo, enquanto outro canal permanece utilizável. Não desligam um telefone real nem comprovam retorno de uma sessão Baileys real.

As evidências compactas estão em `docs/evidence/whatsapp-reliability-20261105/`. A homologação Linux do commit e a validação real no destinatário ainda são gates separados da publicação.

O critério de observação posterior será uma janela mínima de 24 horas com amostras passivas por conexão, cobertura de reinício/deployment e teste autorizado de ida e volta. Critérios: nenhuma sessão assumida por dois responsáveis, nenhum novo QR sem logout comprovado, nenhum processo antigo enviando após perda da lease, recuperação transitória sem duplicar operações, filas sem perda silenciosa, nenhuma regressão de isolamento e recursos sem crescimento contínuo sob carga comparável. Se a janela estiver incompleta ou houver somente tráfego baixo, o estado permanece **em observação**, sem afirmar capacidade ou estabilidade geral.

A varredura automática Deep Scan não iniciou: `Deep Scan cannot safely start a read-only worker: the parent must provide a managed filesystem permission profile.` Não há relatório automático nem cobertura atribuída a essa ferramenta. A revisão de engenharia e os testes descritos são independentes.

## Referências do protocolo

- [Troubleshooting oficial do Baileys](https://github.com/WhiskeySockets/docs/blob/main/advanced/troubleshooting.mdx).
- [Release fixada 6.7.24](https://github.com/WhiskeySockets/Baileys/releases/tag/v6.7.24).
- [Defeito de buffer inicial relatado na versão 6.7.24](https://github.com/WhiskeySockets/Baileys/issues/2810). O relato upstream não comprova ocorrência no Orion.
