# Isolamento da fixture de envio manual — verificação em 05/11/2026

## Causa observada

O FATIA completo da PR 266 (`f9b4827358c33323bb85a241551ce98123007127`, job 127412537341) terminou com 190 falhas, 43 erros, 9.869 aprovações e 10 testes ignorados. Os 212 IDs da última base completa (PR 262, candidata 2) permanecem; os 21 IDs adicionais pertencem exclusivamente a `tests/unit/test_subscription_direct_api.py`.

`include_plan_router` em `app/plan_routing.py` acrescenta a guarda legítima `enforce_endpoint_plan` às rotas compartilhadas. Depois de `app.main` ser importado por outro teste, a fixture antiga incluía esse mesmo roteador e devolvia um ator sintético sem vínculo de empresa. A guarda real respondia 403 antes do stub de `owner_transaction`.

A reprodução ASGI, sem banco ou provedor, confirmou 413/404 antes do registro e 403/403 após o registro, com zero consultas ao banco sintético no segundo caso. Isolar a montagem dos endpoints restituiu 413/404 e preservou todas as guardas no roteador de produção.

## Alteração autorizada

Somente `tests/unit/test_subscription_direct_api.py::client` foi modificado. Segue o padrão existente de `test_subscription_bulk_api.py`: registra os endpoints do controlador em uma aplicação de teste isolada, conservando métodos e status. Nenhuma asserção ou guarda do produto foi retirada.

A alteração está no working tree e ainda não foi incorporada às candidatas das PRs 266 ou 268. Não houve stage, commit ou push por este agente.

## Resultados reais

| Código da fixture | Registro de `app.main` antes dos testes | Resultado |
| --- | --- | --- |
| Anterior | Sim | 21 falhas, 3 aprovações, 1 warning em 0,73 s |
| Corrigido | Não | 24 aprovações em 1,43 s |
| Corrigido | Sim | 24 aprovações, 1 warning em 0,35 s |

O processo com registro prévio também verificou as sete guardas de produção antes e depois dos testes: `production_router_guards_preserved: true`. `git diff --check` passou.

São execuções focadas sobre o mesmo recorte; não se somam às contagens dos gates de CI nem representam uma reexecução da suíte geral.

## Comandos

Executados em `/workspace/scratch/4f4b9f3a0243/orion-assistente`.

Processo limpo, após a correção:

```sh
PYTHONDONTWRITEBYTECODE=1 /workspace/scratch/9a6669713d93/orion_testenv/bin/python -m pytest -q -ra -p no:cacheprovider tests/unit/test_subscription_direct_api.py
```

Registro real antes do recorte (executado antes e depois da correção; na versão posterior também se verifica a guarda ao final):

```sh
PYTHONDONTWRITEBYTECODE=1 /workspace/scratch/9a6669713d93/orion_testenv/bin/python - <<'PY'
from app.main import app
from app import subscription_direct_messages as direct
from app.plan_routing import enforce_endpoint_plan
import pytest
assert all(any(dep.dependency is enforce_endpoint_plan for dep in route.dependencies) for route in direct.router.routes)
result = pytest.main(['-q', '-ra', '-p', 'no:cacheprovider', 'tests/unit/test_subscription_direct_api.py'])
assert all(any(dep.dependency is enforce_endpoint_plan for dep in route.dependencies) for route in direct.router.routes)
print('production_router_guards_preserved: true')
raise SystemExit(result)
PY
```

Logs integrais: `stage14-direct-fixture-before.log`, `stage14-direct-fixture-after-clean.log` e `stage14-direct-fixture-after-registered.log`, todos no diretório de trabalho externo ao repositório.
