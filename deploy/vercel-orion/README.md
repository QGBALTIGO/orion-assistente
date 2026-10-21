# Site e painel Orion na Vercel

Projeto `orion-painel`, equipe `team_fQqbFglYR8GWwMJVVF4KIL99`, domínio reservado
`https://orion-painel-iota.vercel.app`. Esta pasta contém fontes de empacotamento;
não publique o repositório inteiro nem esta pasta diretamente.

O pacote é preparado por `scripts/prepare_vercel_orion.py` em uma pasta externa.
A lista de arquivos é fechada: configuração, página inicial, uma variante do JS do
painel e três respostas estáticas. O manifesto registra hashes e fica fora de
`public/`. Não entram credenciais, documentos de auditoria, backups ou sessões.

O site e o painel usam as mesmas APIs no Railway por rewrites nativos. Nenhum
servidor, banco, worker, bridge QR ou volume é criado na Vercel. A página inicial
tem metadados do novo domínio. O painel e os demais assets continuam vindo da
versão atual do Railway, exceto a variante explicitamente gerada do JS.

## Origem, sessão e privacidade

Requisições de alteração só seguem ao upstream quando Host e Origin são
exatamente os do domínio autorizado. Apenas então a origem é traduzida para o
destino fixo no Railway. Origem ausente, origem diferente e preflight CORS são
recusados. Cookies e o cabeçalho CSRF continuam sob validação do Orion; não há
token administrativo no pacote. Uma publicação de teste não pode realizar
alterações usando seu hostname temporário.

O novo domínio exige um login próprio. Não copiar cookies nem invalidar as
sessões do endereço anterior. Respostas têm cache privado desativado no navegador
e na CDN. Rotas internas, webhooks, documentação de API e arquivos do repositório
não são expostos pelo proxy. O destino não pode ser escolhido pelo cliente.

A conexão inicial/reconexão do Google Agenda continua pelo painel original. A
variante do JS mostra um link explícito para a mesma empresa na agenda original;
não inicia OAuth neste domínio. O callback e a iniciação do Google no novo host
são bloqueados para não consumir estados com cookies no domínio errado.
`PANEL_PUBLIC_URL`, Google e links emitidos pelo backend continuam na configuração
atual. A configuração de campanhas e envios não é alterada.

## Validação e publicação

1. Rodar os testes do pacote e das rotas em ambiente isolado, incluindo contrato
   real de sessão/CSRF e o limite de imagem do backend com armazenamento fictício.
2. Preparar o pacote em pasta externa; revisar a lista e hashes. Conferir a
   identidade/equipe e o projeto explicitamente antes de qualquer publicação.
3. Conferir os arquivos com `vercel deploy --dry --json`. Publicar para produção
   com `--skip-domain`, sem conectar o repositório ou importar variáveis Railway.
4. Conferir a publicação exata: páginas, assets, respostas de autenticação sem
   sessão, bloqueio de rotas/origens e cache. Promover somente esse artefato.
5. Abrir o domínio público e conferir o formulário de login; o operador realiza
   o login. A revisão autenticada é somente leitura e não envia mensagens.
6. Comparar IDs e configuração dos serviços Railway antes/depois. Não reiniciar
   o backend nem alterar dados para fazer testes.

Testes estáticos de regras não comprovam o roteamento efetivo da plataforma.
Testes ASGI com 8 MiB comprovam o backend isolado, e não o tamanho máximo aceito
pelo proxy na rede. Não usar upload ou formulário de produção como teste desse
limite. A limitação operacional documentada do proxy é timeout de 120 segundos.
Envio, entrega, OAuth e transações reais precisam dos aceites próprios da missão.

## Reversão

O endereço Railway continua funcionando independentemente desta publicação.
Reverter somente a publicação/alias deste projeto Vercel para um deployment
anterior verificado. Não mudar DNS Railway, variáveis, sessões ou volumes para
reverter esta frente. Não comprar domínios ou alterar o plano para publicar.

Referências: [rewrites e transformações](https://vercel.com/docs/routing/rewrites),
[configuração](https://vercel.com/docs/project-configuration/vercel-json),
[limites](https://vercel.com/docs/limits).
