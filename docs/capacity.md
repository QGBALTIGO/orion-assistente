# FATIA 7 — Ensaio com 15 barbearias sintéticas

Teste `test_fifteen_tenants_simultaneously_isolated` cria quinze tenants
descartáveis no PostgreSQL, quinze canais de TESTE e contadores de uso.
A prova RLS executa escritas/leitura em paralelo, isoladas pelo
tenant autenticado. Cada tenant acessa exclusivamente sua própria
linha, e consultas sem contexto não veem registros.

O relatório GET /api/v1/admin/usage exige suporte autenticado e retorna
apenas totais; nunca entrega nomes de clientes ou corpos de mensagens.

**Isso NÃO comprova capacidade de uma hospedagem Railway para 15 empresas.**
Antes da expansão real, exigir piloto aprovado, testes de carga com tráfego
consentido ou sintético, métricas CPU/RAM/disco, filas Arq, p95/p99,
reentregas de webhooks, disponibilidade, limites Meta por categoria,
alertas de incidente, backup cifrado offsite e restore ensaiado.
É preciso medir custos de suporte humano e mensagens efetivas por tenant.

O script de carga não envia mensagens Meta reais nem gera cobranças.
Escalabilidade e custo real continuam desconhecidos.
