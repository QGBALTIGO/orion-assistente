# Metadados do ingresso HTTPS controlado

O middleware ASGI `AuthenticatedIngressMiddleware` permite que o ingresso
controlado preserve o IP do visitante e o Host público quando se conecta à
origem por seu nome técnico. Isso não autentica usuários, não concede acesso
a empresas e não substitui sessão, CSRF ou limites de uso.

## Ativação

Por padrão, `ORION_INGRESS_TOKEN` e `ORION_INGRESS_PUBLIC_HOST` estão vazios e o
encaminhamento fica desativado. Configure ambos apenas na API e no ingresso
autorizado; configuração parcial ou inválida impede a inicialização do
middleware. A mensagem de erro não contém os valores configurados.

- Gere um segredo independente com `secrets.token_urlsafe(32)` e armazene-o
  somente nos ambientes privados. O formato aceito é base64url canônico de
  32 bytes, sem padding, com 43 caracteres. A validação de formato não comprova
  aleatoriedade: não escolha uma sequência previsível.
- `ORION_INGRESS_PUBLIC_HOST` contém somente o hostname público ASCII, por
  exemplo `orionagenda.pro`, sem URL, caminho, porta, usuário ou espaços.
  O valor é normalizado para minúsculas. Ele deve corresponder à origem pública
  já autorizada da aplicação; não ampliar allowlists ou alterar segredos de sessão.
- Ambos são lidos na criação do middleware. Uma mudança exige recriar o
  processo da API pelo fluxo normal de publicação.

O ingresso deve sobrescrever, em cada requisição, exatamente um
`X-Orion-Ingress-Token` e um `X-Orion-Client-IP`. O segundo deve vir do endereço
real do peer, nunca de um cabeçalho fornecido pelo visitante. Só a credencial
correta e um IP literal válido ativam o encaminhamento. IPv6 é normalizado;
listas, portas, zone IDs e valores inválidos são rejeitados.

Uma requisição aceita modifica `scope.client`, o cabeçalho Host e o esquema,
usando o hostname fixo do ambiente e HTTPS (`wss` para WebSocket). Isso evita
que redirecionamentos automáticos voltem para HTTP após a terminação TLS da
origem. O esquema nunca é escolhido por `X-Forwarded-Proto` ou `Forwarded`.
A porta de transporte, quando existente,
é preservada e não representa uma porta autenticada do visitante.
Origin, cookies, cabeçalhos CSRF, caminho, query e corpo não são reescritos.
Sem o par autenticado válido não há alteração de Host, cliente ou esquema.
Os dois cabeçalhos dedicados são removidos antes de qualquer handler, inclusive
quando o recurso está desativado ou a autenticação é recusada.

## Contrato com Nginx

A configuração específica versionada está em
[`infra/nginx/orionagenda-pro.conf`](../infra/nginx/orionagenda-pro.conf).
Ela referencia um snippet privado, com o token e `X-Orion-Client-IP` preenchido
por `$realip_remote_addr`; o segredo nunca pertence ao repositório. Não executar
`nginx -T` depois de instalar esse snippet, pois esse comando imprime includes.

Trecho ilustrativo para o vhost específico, a revisar com a configuração real;
não é um comando de instalação nem altera outros vhosts:

```nginx
resolver 1.1.1.1 1.0.0.1 valid=30s ipv6=off;
set $orion_origin gftl16d0.up.railway.app;

proxy_pass https://$orion_origin;
proxy_set_header Host orion-assistente-production.up.railway.app;
proxy_ssl_server_name on;
proxy_ssl_name orion-assistente-production.up.railway.app;
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;

proxy_set_header X-Orion-Ingress-Token "<segredo privado configurado na API>";
proxy_set_header X-Orion-Client-IP $remote_addr;
```

O destino continua sendo resolvido por nome; não congelar o IP do Railway.
O destino `gftl16d0.up.railway.app` pertence ao domínio personalizado mantido no
Railway: não remover essa associação ao alterar o DNS público para o ingresso.
SNI e Host de transporte usam o domínio técnico, cujo certificado não depende
do DNS público apontar diretamente para o Railway. Somente depois de autenticar
os metadados a aplicação restabelece o Host público configurado no ambiente.

Não habilitar `trust *`, `FORWARDED_ALLOW_IPS=*` nem confiança aberta em
`Forwarded`, `X-Forwarded-For` ou `X-Forwarded-Host`. Antes de usar `$remote_addr`,
verifique que regras globais `real_ip_header`/`set_real_ip_from` não permitem
substituí-lo por entrada de um visitante arbitrário. Não usar variáveis
`$http_x_orion_client_ip`, `$http_x_forwarded_for` ou equivalentes para preencher
o cabeçalho dedicado. Não registrar o token em logs, URLs, telas ou respostas.

O middleware não remove nem amplia a confiança de proxy já configurada no
servidor ASGI. A configuração existente de transporte deve ser revisada
separadamente, sem abrir confiança para todos os peers como atalho.

## Verificação e reversão

Os testes unitários são inteiramente ASGI/sintéticos, sem banco, Redis,
mensagens ou chamadas a provedores. Cobrem clientes distintos, spoofing,
duplicatas, configuração inválida, recurso desligado e preservação de
Origin/CSRF/cookies/caminho/query. Não comprovam a configuração de um Nginx real.

Antes de publicação, conferir o vhost e TLS da origem, ausência de cache para
rotas autenticadas, IP real em limites de uso e preservação das sessões no
fluxo completo. Para desativar, remover ambos os valores e recriar apenas a API;
não trocar as credenciais de sessão, desconectar integrações ou mexer no banco.
