# Orion Vercel — fachada sempre atualizada

Projeto `orion-painel`, domínio público `https://orion-painel-iota.vercel.app`, backend e interface atualizados no Railway em `https://orion-assistente-production.up.railway.app`.

## Como funciona

A Vercel atua somente como uma camada de roteamento e segurança. **Página inicial, HTML do painel, JavaScript, CSS, mídias, site público e APIs são encaminhados para o Orion atual da Railway.** Não existe cópia própria do painel ou da landing page na Vercel; novas atualizações da Railway ficam visíveis no domínio Vercel na próxima requisição, sem necessidade de novo deploy Vercel. Políticas `no-store` evitam cache desatualizado da borda.

O arquivo `vercel.json` mantém as restrições de host e Origin para operações que escrevem dados, validações de CSRF pelo backend, bloqueios de caminhos internos e proteção contra métodos não permitidos. O Google Agenda continua iniciando autenticação exclusivamente no domínio Railway, pois o OAuth callback registrado pertence a ele; o JavaScript principal detecta o hostname da fachada e orienta o cliente. A homepage Railway utiliza o marcador fixo `x-orion-facade: vercel-iota` para gerar metadados canônicos e Open Graph da Vercel.

O banco PostgreSQL, Redis, workers e WhatsApp continuam exclusivos da Railway. Os dois domínios consultam os mesmos dados. Nenhuma mudança de autenticação ou sessão foi introduzida.

## Quando publicar a Vercel

**Mudanças normais do Orion não exigem redeploy Vercel.** Apenas alterações na política de roteamento ou nas respostas locais `403`, `404` e `google-origin` requerem um novo artefato Vercel.

Para preparar esse artefato somente de política:

`python -m scripts.prepare_vercel_orion --output <diretorio-fora-do-repositorio>`

O preparador inclui somente quatro arquivos: `vercel.json`, `403.html`, `404.html`, `google-origin.json`. Um manifesto privado registra hashes. O pacote não possui HTML de landing ou JS do painel que possa ficar antigo.

Antes de trocar a política, validar em preview; conferir rotas, status, headers, cache e ausência de chamadas de OAuth; depois promover em produção. Não executar operações comerciais reais durante os testes.

## Testes e garantia contra regressões

`pytest -q tests/test_vercel_orion_bundle.py tests/test_vercel_orion_routes.py tests/unit/test_landing_api_unit.py`

`node --test ui-tests/vercel-facade.test.mjs`

O workflow `orion-vercel-parity.yml` realiza comparações somente leitura a cada seis horas e em alterações relevantes na branch main, verificando paridade de bytes do frontend em ambos os hosts. As únicas diferenças previstas são os metadados canônicos do domínio Vercel, gerados na mesma aplicação Railway.

## Recuperação

Uma alteração defeituosa na política de proxy pode ser revertida na Vercel sem tocar em Railway, PostgreSQL, sessões ou uploads. Nunca restaurar política antiga que sirva JavaScript estático congelado.
