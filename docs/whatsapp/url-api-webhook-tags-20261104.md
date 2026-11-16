# Tags de URL, API e webhook — 04/11/2026

Escopo: o bloco **URL, Servidor Externo ou Webhook** do Chatbot, seu botão de
leitura de tags, contrato privado de retorno e preenchimento de texto/TXT.
Não altera autenticação de webhooks Cakto, envios de agendamento, sessões Baileys
ou os planos/nichos que podem acessar o Chatbot.

## Defeitos reproduzidos

A descoberta só examinava as chaves da raiz, incluindo objetos que o renderizador
não conseguia substituir. Backend e formulário proibiam caminhos aninhados; a
ponte e o recibo privado recusavam uma lista JSON na raiz. Falhas de autenticação,
método, redirecionamento e JSON eram reduzidas a uma indisponibilidade genérica.
As tags consultadas continuavam visíveis após mudar a URL/método no editor.

Na base `23b78bf42086766694a992474f078c2a4ed0a2eb`, oito testes novos de descoberta
(GET/POST) e doze testes novos do formulário reproduziram as limitações.

## Contrato corrigido

- `{{campo}}` mantém o comportamento anterior; `{{ campo }}` aceita espaços.
- `{{data.cliente.nome}}` acessa somente chaves JSON, com caixa exata.
- `{{itens[0].preco}}` e `{{[0].reply}}` acessam índices não negativos.
- `{{["nome completo"]}}`, `{{["pay-url"]}}` e `{{["user.name"]}}` acessam
  nomes literais. `user.name` sem colchetes é um caminho, nunca uma aproximação.
- A descoberta retorna somente caminhos para texto, número ou booleano; não
  expõe os valores. Zero, false e texto vazio são preservados. Objetos, listas
  intermediárias e null não viram tags utilizáveis.
- Campo ausente/null bloqueia a resposta derivada; não manda a expressão crua,
  não inventa um valor e não busca dados de outra operação.
- Sem template, mantém **reply como texto na raiz**. Respostas encapsuladas ou
  listas precisam de template explícito, sem adivinhar entre message/data/reply.
- Valores do provedor são dados: não há avaliação de código, atributos de objeto,
  expressões, curingas ou expansão recursiva de tags dentro de valores retornados.
- Limites: 128 tags descobertas, 512 unidades UTF-16 por caminho, 16 níveis,
  8.192 nós JSON, 512 KiB por resposta. A expansão tem orçamento verificado antes
  de montar o texto, evitando multiplicação descontrolada de um valor grande.
- Identificadores numéricos acima do inteiro seguro JavaScript devem vir em string;
  não arredondar silenciosamente na ponte nem aceitar uma descoberta incompatível.
- Erros da consulta são códigos locais allowlisted com texto estático: acesso
  recusado, método, redirecionamento, Content-Type, JSON, tamanho, timeout e rede.
  Não devolvem corpo, cabeçalhos ou segredos do servidor remoto.
- Trocar URL ou método remove os nomes descobertos anteriormente.

## Segurança e validação

HTTPS/certificado válido, bloqueio SSRF, DNS/IP fixado, ausência de redirects,
limites e autorização antes/depois da consulta permanecem. Ler tags exige ação
explícita, envia somente o contexto sintético existente e não envia WhatsApp.
Não adicionar tentativas automáticas de POST ou repetir efeitos após timeout.

Testes cobrem um mesmo vocabulário backend/formulário, raiz lista, chaves
especiais, XSS, dados ausentes, números/booleanos, orçamento de expansão e
idempotência/autorização no PostgreSQL. Os testes usam servidores simulados;
não foram consultadas URLs particulares reais nem enviadas mensagens a clientes.
Entrega/renderização no WhatsApp e incidentes de criptografia são verificações
separadas; esta entrega não declara esses incidentes resolvidos.
