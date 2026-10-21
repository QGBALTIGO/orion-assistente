# Publicação do site e painel — 20/10/2026

Site: https://orion-painel-iota.vercel.app/

Painel: https://orion-painel-iota.vercel.app/panel

## Resultado verificado

O deployment `dpl_4NZRWqmSHdFgrdyM4YdeYPnnyXQr`, em estado READY e target
production, foi publicado inicialmente sem o domínio reservado, conferido e
promovido sem rebuild. Projeto `prj_Vjcto1OleQPNxi0uTXEC1C28Q3Ma` / `orion-painel`,
equipe `team_fQqbFglYR8GWwMJVVF4KIL99` / `kaykysousa3-5893`, plano Hobby.
O domínio gratuito está verificado. Não houve compra, upgrade ou importação de
credenciais do Railway.

A conta estava acessível pelo login já salvo da ferramenta oficial Vercel CLI
65.0.0, embora o conector instalado não tivesse o grant da equipe e retornasse
403. Não foi necessário pedir senha, token ou reinstalação do plugin.

Página inicial e tela de login abertas no navegador, sem avisos/erros de console.
Os screenshots não contêm credenciais ou dados de clientes. A consulta HTTPS
pública, sem autenticação Vercel, confirmou o mesmo HTML e JS do pacote testado,
assets, APIs protegidas sem sessão, bloqueios de origem, internos e OAuth, e
cache privado. Novo login autenticado neste domínio foi solicitado ao operador
e permanece pendente neste registro; isso não invalida a consulta autenticada
anterior feita no endereço Railway.

## Índice de progresso e evidências

| Etapa | Estado / evidência |
|---|---|
| Código atual e requisitos de origem/cookie/OAuth | Inspecionados; `vercel-auth-inspection.json`, 19 casos ASGI isolados |
| Pacote fechado e metadados | `bundle-manifest.json`: seis saídas e hashes; manifesto fora de public |
| Testes do preparador | 18 passaram, 1 ignorado: criação de symlink indisponível neste Windows (`vercel-bundle-tests.xml`) |
| Rotas, sessão, CSRF e mídia em isolamento | 214 passaram, sem falhas ou pulados (`vercel-routes-v2.xml` e `.log`) |
| Regressão completa da interface | 332 passaram, sem falhas ou pulados (`vercel-dom-full.log`); inclui 3 contra o JS realmente gerado |
| Primeiro candidato | 17/18 HTTP; OPTIONS de arquivo estático virou 204, apesar do status configurado; candidato não promovido (`vercel-candidate-http-receipt.json`) |
| Correção da diferença da plataforma | OPTIONS agora é uma resposta 403 terminal sem arquivo; gate e asserção preservados |
| Segundo candidato e publicação pública | 18/18 HTTP em cada endereço (`vercel-candidate-v2-http-receipt.json`, `vercel-public-http-receipt.json`) |
| Preservação do Railway | Cinco deployments iguais e online; configurações API/worker iguais; nenhum patch pendente (`vercel-railway-preservation.json`) |
| Revisão visual | `vercel-public-home.png` e `vercel-public-login.png`; sem submissão de formulário |
| Login real no domínio Vercel | Pendente de conclusão pelo operador |

As contagens representam conjuntos e ambientes distintos; não representam
homologação de todas as integrações. A inspeção de headers foi executada na
plataforma real; o comportamento autenticado completo ainda precisa do login.

O pacote publicado tem configuração SHA256
`10a79d6a85010de66d51b45e6d8d894463f2941132383ac6dba1af1520ccbbdd`,
index SHA256 `5285c4bac3ccf06c6d0d8950d3cc7647cf8f2c1ec946492e2781421ec53a5648`
e JS SHA256 `0dec8087f9d9894cf4288d4f75b110b28e217d7c3fb0b17a628dfbfe3a7c7c32`.
O índice `evidence-index.json` lista os hashes dos recibos preservados.

## Preservação e limites

Nenhuma alteração ou publicação foi feita no Railway nesta etapa. API e worker
continuam configurados para `a85da1c`; seus deployments continuam `47000175` e
`0bff9fba`. QR `5d6bf01f`, PostgreSQL `47bbb965` e Redis `b369ead8` não reiniciaram.
Não houve acesso a volumes, exclusão/copiação de cookies, alteração de dados de
negócio, envio real de teste ou ativação de campanhas. O tráfego normal da IA não
foi interrompido nem contabilizado como parte dos testes.

Rewrites nativos não duplicam backend ou banco. Nenhum segredo administrativo é
injetado nas requisições do cliente. Google conecta/reconecta pelo painel
original por link explícito, pois seu callback e nonce continuam no domínio
Railway. Links emitidos pelo backend também conservam sua origem configurada.

O teste de 8 MiB validou o upload real do backend com bucket/Redis fictícios;
o limite de transporte da borda Vercel **não foi homologado com upload real**.
Também não foram executados OAuth, lead, convite, alteração cadastral, mensagem,
campanha ou operação de atendimento reais para testar a nova frente. Esses
aceites continuam separados dos testes isolados e da publicação do site.

Fontes e operação: [procedimento](../../../deploy/vercel-orion/README.md),
[configuração](../../../deploy/vercel-orion/vercel.json),
[preparador](../../../scripts/prepare_vercel_orion.py).
