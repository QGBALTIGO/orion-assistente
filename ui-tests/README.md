# Testes de DOM do Orion

Este pacote executa o JavaScript real da suíte e do painel num DOM isolado (`jsdom 26.1.0`) e usa respostas de API sintéticas. Ele não inicia navegador, acessa a conta conectada, usa CDP, envia mensagens ou chama serviços externos.

```sh
cd ui-tests
npm ci --ignore-scripts --no-audit --no-fund
npm test
```

Os **31 casos** cobrem montagem sem duplicação de eventos, respostas atrasadas após troca de empresa, consulta sem gravação para leitores, confirmação de exclusão, o adaptador real de HTTP 204, geração de IA somente após ação explícita, convites sem link recuperável e aprovação versionada. O site público é exercitado com o HTML e JavaScript reais: dados tratados como texto, links HTTPS, prévia sem reservas, combo com adicional que não aumenta a duração, consentimento explícito e descarte de horários recebidos fora de ordem. Todos os nomes, contatos, cotações e agendamentos dos mocks são sintéticos.

Os casos de `booking-recovery.test.mjs` também verificam identidade de cliente existente sem telefone, recuperação de respostas perdidas de hold e confirmação, fatos congelados durante uma tentativa incerta, seleção autenticada de adicionais, guardas de troca de empresa, chaves de IA vinculadas ao conteúdo, fuso da empresa, dias da semana e folgas, bloqueio de publicação para editor, campanha arquivada, conta e marcos de onboarding.

Os seis casos de `mobile-atendimento.test.mjs` usam `panel-harness.mjs` para executar o HTML completo, os dois scripts inline e a suíte real juntos. Eles verificam a ordem e unicidade da navegação, links antigos e acesso à administração, bloqueio do WhatsApp para membros, CSRF e expiração do pareamento, busca local, retorno aos contatos, rascunhos separados por destinatário, limpeza no logout e descarte de consultas atrasadas após novo login. O envio manual usa somente transporte sintético: cancelar não envia, a tentativa pendente bloqueia troca/edição, uma aceitação não é perdida se a conexão mudar e apenas o rascunho enviado é limpo. O harness registra tentativas de origem externa e mutações sem resposta sintética explícita como erros, mesmo quando o controlador captura a rejeição.

O `harness.mjs` oferece `makeSuite`, `flush`, `deferred`, `click`, `fill` e `submit` para casos adicionais. O `public-harness.mjs` fornece o DOM do site público. No Node 24 usado nesta validação, `npm run test:details` executa no mesmo processo e apresenta os casos individuais quando o ambiente agrega o relatório normal por arquivo; cada teste mantém seu próprio DOM e limpa-o ao terminar.

Os testes não verificam layout, tamanho de elementos, contraste computado, fontes renderizadas, toque real ou equivalência visual. A decisão de preservar a posição de leitura é exercitada com medidas de viewport sintéticas. A geometria real e as demais verificações visuais precisam de uma prévia HTTP(S) da versão e inspeção nos tamanhos de tela previstos.
