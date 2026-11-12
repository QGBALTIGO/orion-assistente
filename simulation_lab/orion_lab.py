"""Orion Simulation & Reliability Lab: isolated, deterministic synthetic workloads.

No production network access, no WhatsApp sending and no database writes.
Generated cases are NOT evidence of executing the Orion application.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import random
import statistics
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from simulation_lab.utterances import OPERATION, render_message

QUOTAS = {
    "agenda": 22000,
    "professionals_services_commissions": 10000,
    "whatsapp_ai_notifications": 14000,
    "finance_performance": 10000,
    "crm_history": 8000,
    "recurrence": 6000,
    "public_ux": 8000,
    "onboarding_plans": 5000,
    "external_integrations": 5000,
    "tenant_security": 8000,
    "failures_performance": 4000,
}
TOTAL = sum(QUOTAS.values())
DURATIONS = (10, 15, 20, 30, 40, 45, 60, 75, 90, 120, 180, 240)
SERVICES = (
    ("corte", 40, 4000), ("barba", 20, 2500), ("sobrancelha", 15, 1200),
    ("lavagem", 15, 2000), ("hidratacao", 75, 10000),
    ("escova", 45, 6500), ("coloracao", 180, 25000),
    ("mechas", 240, 35000), ("manicure", 60, 4500),
    ("cachos", 90, 16000), ("tratamento", 120, 18000),
)
COMPANY_TYPES = (
    "barbearia_solo", "barbearia_dupla", "barbearia_cinco", "barbearia_dez",
    "barbearia_mista_fila", "barbearia_agendada", "salao_pequeno",
    "salao_multiequipe", "salao_feminino", "salao_masculino",
    "salao_unissex", "salao_manicure", "salao_cachos",
    "salao_coloracao", "salao_procedimentos_longos", "expedientes_diferentes",
)
INTENTS = {
    "agenda": ("consultar_horarios", "reservar", "encaixar", "alterar_profissional",
               "reservar_combo", "agendar_familiar", "bloquear", "remarcar",
               "cancelar", "pedir_antes_da_abertura", "pedir_apos_fechar"),
    "professionals_services_commissions": ("criar_servico", "editar_preco", "alterar_duracao",
               "combinar_servicos", "editar_comissao", "alterar_meta",
               "remover_servico", "trocar_profissional", "bloquear_ferias"),
    "whatsapp_ai_notifications": ("saudacao", "consultar_preco", "reservar_por_chat",
               "confirmar_botao", "cancelar_botao", "lista_servicos",
               "mensagem_citada", "audio", "reconectar", "retry_envio",
               "notificar_profissional", "avaliacao", "lembrete"),
    "finance_performance": ("pagar", "desconto", "estorno", "pagamento_parcial",
               "produto_extra", "servico_extra", "comissao_fixa", "comissao_percentual",
               "ticket_medio", "filtro_periodo", "meta_profissional"),
    "crm_history": ("cliente_novo", "cliente_fixo", "cliente_em_risco",
               "unificar_whatsapp_site", "telefone_brasileiro", "mesmo_nome",
               "historico", "aniversario", "total_gasto", "origem_cadastro"),
    "recurrence": ("criar_serie", "editar_uma", "editar_todas", "cancelar_uma",
               "cancelar_todas", "ferias", "feriado", "trocar_profissional",
               "fim_da_serie", "conflito_futuro"),
    "public_ux": ("troca_tema", "foto_quebrada", "combo_mobile", "carrossel",
               "teclado_aberto", "zoom", "data_indisponivel", "nome_longo",
               "conexao_lenta", "mapa", "compartilhar", "formulario"),
    "onboarding_plans": ("criar_empresa", "validar_cep", "numero_obrigatorio",
               "cadastrar_profissional", "configurar_expediente", "configurar_canal",
               "upgrade", "downgrade", "webhook_duplicado", "retomar_cadastro"),
    "external_integrations": ("calendar_token_expirado", "calendar_timeout",
               "calendar_duplicado", "calendar_remarcacao", "cakto_assinatura",
               "cakto_assinatura_invalida", "whatsapp_indisponivel",
               "viacep_timeout", "reconciliacao"),
    "tenant_security": ("ler_cliente_alheio", "cancelar_reserva_alheia",
               "ver_financeiro_alheio", "editar_servico_alheio", "reutilizar_token",
               "elevar_privilegio", "convite_revogado", "id_manipulado",
               "rls", "alterar_nascimento_publico"),
    "failures_performance": ("postgres_offline", "redis_offline",
               "worker_reinicia", "timeout", "retentativa", "falha_apos_commit",
               "concorrencia", "mensagem_duplicada", "fila_atrasada",
               "latencia_alta", "modelo_indisponivel"),
}
CHANNELS = ("site", "whatsapp", "painel", "worker")
PERSONAS = ("cliente_novo", "cliente_semanal", "cliente_mensal",
            "cliente_irritado", "cliente_atrasado", "cliente_fixo",
            "responsavel", "profissional", "proprietario", "administrador")
FAILURE_TYPES = ("none", "redis_timeout", "db_timeout", "message_duplicate",
                 "worker_restart", "api_5xx", "model_unavailable",
                 "stale_availability", "token_expired")
LANGUAGE = ("formal", "coloquial", "abreviado", "erro_digitacao",
            "audio_transcrito", "mensagem_curta", "mensagem_longa")
STATUS = ("agendado", "confirmado", "em_andamento", "concluido",
          "cancelado", "nao_compareceu", "remarcacao_pendente")
BASE_DATE = date(2026, 11, 16)
SEED = 2601112


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def ident(namespace, key):
    return str(uuid5(NAMESPACE_URL, "orion-simulation-lab:" + namespace + ":" + str(key)))


def company_profile(number, rng):
    kind = COMPANY_TYPES[number % len(COMPANY_TYPES)]
    size = (1 if "solo" in kind else 2 if "dupla" in kind else
            5 if "cinco" in kind else 10 if "dez" in kind else rng.randint(2, 8))
    return {
        "synthetic": True, "tenant_id": ident("tenant", number),
        "display_name": "Empresa ficticia %04d" % number, "kind": kind,
        "timezone": rng.choice(("America/Campo_Grande", "America/Sao_Paulo",
                                "America/Manaus")),
        "professionals": [
            {"id": ident("pro", f"{number}-{i}"),
             "role": rng.choice(("barbeiro", "cabeleireiro", "colorista", "manicure")),
             "starts_min": rng.choice((480, 510, 540, 600)),
             "ends_min": rng.choice((1020, 1080, 1140, 1200)),
             "lunch": rng.choice(((720, 780), (750, 810), (0, 0))),
             "off_weekdays": [rng.randint(0, 6)],
             "commission_basis_points": rng.randint(1500, 5500)}
            for i in range(size)
        ],
        "services": [name for name, _, _ in rng.sample(SERVICES, k=rng.randint(4, 9))],
        "whatsapp_connected": rng.choice((True, False)),
        "google_calendar_connected": rng.choice((True, False)),
        "plan_fixture_key": rng.choice(("fixture_a", "fixture_b", "fixture_c")),
    }


def clients_for_company(company_id, rng, n=16):
    for index in range(n):
        yield {
            "synthetic": True,
            "tenant_id": company_id,
            "customer_id": ident("customer", f"{company_id}/{index}"),
            "display_name": "Cliente sintetico %d" % index,
            "phone": None,  # No real/reachable phone number is generated.
            "email": f"cliente-{index}@example.invalid",
            "persona": rng.choice(PERSONAS[:7]),
            "frequency_days": rng.choice((0, 7, 14, 30, 60, 90)),
            "missed_visits": rng.randint(0, 3),
            "preferred_professional": rng.choice((None, 0, 1)),
        }


def interval_overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def bookable(state):
    """Reference oracle only. Not a call to Orion's booking implementation."""
    start, end = state["start_min"], state["start_min"] + state["duration_min"]
    if not 0 < state["duration_min"] <= 1440 or start < 0 or end > 1440:
        return False
    if not state["manual_override"]:
        if state["weekday"] == state["day_off"]:
            return False
        if start < state["opens_min"] or end > state["closes_min"]:
            return False
        if interval_overlaps((start, end), state["lunch"]):
            return False
    if state["blocked"] and interval_overlaps((start, end), state["block_interval"]):
        return False
    if state["existing"] and interval_overlaps((start, end), state["existing_interval"]):
        return False
    return True


def money_result(state):
    service = state["service_cents"]
    product = state["product_cents"]
    discount = state["discount_cents"]
    total = max(0, service + product - discount)
    service_base = max(0, service - min(discount, service))
    commission = (service_base * state["commission_bp"] + 5000) // 10000
    return {"total_cents": total, "service_commission_cents": commission,
            "commission_base_cents": service_base}


def make_state(area, index, rng):
    chosen = rng.sample(SERVICES, rng.choice((1, 1, 1, 2, 3)))
    start = rng.randrange(450, 1230, 5)
    duration = rng.choice(DURATIONS)
    l_start = rng.choice((690, 720, 750))
    busy_start = start + rng.choice((-90, -40, 0, 20, 40, 60, 90))
    block_start = start + rng.choice((-60, -15, 0, 30, 60))
    service_cents = sum(x[2] for x in chosen)
    state = {
        "intent": rng.choice(INTENTS[area]),
        "channel": rng.choice(CHANNELS),
        "persona": rng.choice(PERSONAS),
        "language": rng.choice(LANGUAGE),
        "professional_role": rng.choice(("barbeiro", "cabeleireiro", "colorista", "manicure")),
        "service_names": [x[0] for x in chosen],
        "duration_min": duration, "requested_service_duration_min": sum(x[1] for x in chosen),
        "start_min": start, "weekday": rng.randrange(7),
        "lead_days": rng.choice((0, 1, 2, 3, 7, 14, 30, 60)),
        "opens_min": rng.choice((480, 510, 540, 600)),
        "closes_min": rng.choice((1020, 1080, 1140, 1200)),
        "lunch": [l_start, l_start + rng.choice((30, 60, 90))],
        "day_off": rng.randrange(7), "manual_override": rng.random() < .04,
        "blocked": rng.random() < .30,
        "block_interval": [block_start, block_start + rng.choice((15, 30, 40, 90))],
        "existing": rng.random() < .37,
        "existing_interval": [busy_start, busy_start + rng.choice(DURATIONS)],
        "service_cents": service_cents,
        "product_cents": rng.choice((0, 0, 0, 2000, 4500)),
        "discount_cents": rng.choice((0, 0, 500, 1000, 2500)),
        "commission_bp": rng.choice((0, 1000, 2500, 4000, 5000, 7500)),
        "status": rng.choice(STATUS),
        "worker_count": rng.choice((1, 2, 5, 10, 50)),
        "retries": rng.randrange(4), "failure": rng.choice(FAILURE_TYPES),
        "series_occurrences": rng.choice((2, 4, 6, 8, 12, 16)),
        "series_every_days": rng.choice((7, 14, 28)),
        "policy_cancel_before_min": rng.choice((0, 30, 60, 120, 1440)),
        "client_arrival_late_min": rng.choice((0, 0, 5, 15, 30)),
        "device": rng.choice(("iphone_small", "iphone_large", "android_small",
                              "android_large", "tablet", "desktop")),
        "theme": rng.choice(("light", "dark", "system")),
        "network": rng.choice(("wifi", "4g", "5g", "slow_3g")),
        "permission": rng.choice(("owner", "admin", "staff", "revoked", "anonymous")),
        "target_tenant": rng.choice(("self", "other", "unknown")),
    }
    # A schedule/behavior tuple is a meaningful scenario difference; identities are excluded.
    return state


def make_scenario(area, index, rng, tenants):
    state = make_state(area, index, rng)
    expected = {
        "appointment_bookable_reference": bookable(state),
        "finance_reference": money_result(state),
        "cross_tenant_access_denied": state["target_tenant"] != "self"
            and state["permission"] != "owner_global_test_only",
        "idempotency_required": state["retries"] > 0 or state["worker_count"] > 1,
        "send_real_messages": False,
        "external_write_allowed": False,
    }
    return {
        "scenario_id": ident("case", f"{area}:{index}"),
        "synthetic": True,
        "area": area,
        "tenant_id": ident("tenant", rng.randrange(tenants)),
        "actor_id": ident("actor", f"{area}:{index}"),
        "state": state,
        "message": render_message(area, state),
        "expected_logical_operation": OPERATION[area],
        "expected": expected,
        "oracle": "synthetic_reference_only",
        "semantic_signature": digest({"area": area, "state": state}),
        "executed_against_orion": False,
    }


def write_jsonl_gz(target, records):
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=6, mtime=0,
                           fileobj=raw) as handle:
            for value in records:
                handle.write((json.dumps(value, ensure_ascii=False,
                    separators=(",", ":")) + "\n").encode("utf-8"))


def read_jsonl_gz(source):
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def make_journey(i, rng, tenants):
    style = i % 10
    workflow = (
        ("site", "consultar_servicos", "selecionar_profissional", "selecionar_horario",
         "revalidar_reserva", "persistir", "notificar", "concluir", "avaliar"),
        ("whatsapp", "interpretar_intencao", "consultar_agenda", "sugerir",
         "confirmar", "persistir", "enviar_resumo", "registrar_historico"),
        ("painel", "criar_profissional", "criar_servico", "definir_expediente",
         "criar_combo", "configurar_comissao", "consultar_financeiro"),
        ("recorrencia", "reservar_serie", "cancelar_uma", "ferias",
         "revalidar_ocorrencias", "remarcar", "recalcular"),
        ("falha", "propor_horario", "outro_cliente_reserva", "revalidar",
         "oferecer_outro", "confirmar", "reconciliar"),
        ("whatsapp", "reservar", "persistir", "desconectar_whatsapp",
         "enfileirar", "reconectar", "retry_idempotente", "notificar"),
        ("financeiro", "atender", "adicionar_servico", "adicionar_produto",
         "aplicar_desconto", "finalizar", "calcular_comissao", "conciliar"),
        ("funcionario", "aceitar_convite", "consultar_agenda", "bloquear",
         "atender", "finalizar", "consultar_meta"),
        ("seguranca", "autenticar_empresa_a", "acessar_empresa_b",
         "negar_acesso", "registrar_tentativa", "verificar_integridade"),
        ("integracao", "agendar", "evento_calendar_falha", "preservar_reserva",
         "reconciliar", "confirmar_estado"),
    )[style]
    return {
        "journey_id": ident("journey", i),
        "synthetic": True, "tenant_id": ident("tenant", rng.randrange(tenants)),
        "persona": rng.choice(PERSONAS), "style": style,
        "date": str(BASE_DATE + timedelta(days=rng.randrange(365))),
        "professional": rng.randrange(1, 11),
        "duration": rng.choice(DURATIONS),
        "channel": rng.choice(CHANNELS),
        "failure": rng.choice(FAILURE_TYPES),
        "steps": [{"order": k, "action": step, "expected_checkpoint": True}
                  for k, step in enumerate(workflow)],
        "executed_against_orion": False,
    }


def generate(out, count=TOTAL, journeys=10000, companies=2048, seed=SEED):
    if count != TOTAL:
        raise ValueError("Release corpus requires exactly 100000 scenarios")
    if journeys < 10000 or companies < 1000:
        raise ValueError("Require >=10000 journeys and >=1000 synthetic establishments")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    write_jsonl_gz(out / "companies.jsonl.gz",
                   (company_profile(i, rng) for i in range(companies)))
    write_jsonl_gz(out / "customers.jsonl.gz",
                   (c for i in range(companies)
                    for c in clients_for_company(ident("tenant", i), rng)))
    unique_signatures = set()
    counts = Counter()

    def cases():
        for area, quota in QUOTAS.items():
            area_rng = random.Random(seed + list(QUOTAS).index(area) * 100003)
            for i in range(quota):
                for attempt in range(150):
                    case = make_scenario(area, i, area_rng, companies)
                    if case["semantic_signature"] not in unique_signatures:
                        unique_signatures.add(case["semantic_signature"])
                        break
                else:
                    raise AssertionError("Insufficient semantic diversity: " + area)
                counts[area] += 1
                yield case

    write_jsonl_gz(out / "scenarios.jsonl.gz", cases())
    write_jsonl_gz(out / "journeys.jsonl.gz",
                   (make_journey(i, rng, companies) for i in range(journeys)))
    manifest = {
        "lab": "Orion Simulation & Reliability Lab",
        "seed": seed, "synthetic": True, "version": 1,
        "generated_scenarios": sum(counts.values()),
        "unique_semantic_signatures": len(unique_signatures),
        "scenario_quotas": dict(counts),
        "generated_journeys": journeys, "synthetic_companies": companies,
        "synthetic_customers": companies * 16,
        "orion_app_tests_executed": 0, "ai_conversations_executed": 0,
        "postgres_integration_tests_executed": 0, "end_to_end_tests_executed": 0,
        "note": "Generation is not execution. No network or production DB is contacted.",
        "files": {},
    }
    for filename in ("companies.jsonl.gz", "customers.jsonl.gz",
                     "scenarios.jsonl.gz", "journeys.jsonl.gz"):
        contents = (out / filename).read_bytes()
        manifest["files"][filename] = {
            "bytes": len(contents), "sha256": hashlib.sha256(contents).hexdigest()
        }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2,
                                      ensure_ascii=False), encoding="utf-8")
    make_report(out, manifest)
    return manifest


def run_reference(out, max_scenarios=100000):
    """Run synthetic reference invariants; explicitly never calls the Orion API."""
    started = time.perf_counter()
    failures = []
    by_area = Counter()
    tested = 0
    elapsed_ms = []
    for case in read_jsonl_gz(out / "scenarios.jsonl.gz"):
        if tested >= max_scenarios:
            break
        t0 = time.perf_counter()
        s = case["state"]
        exp = case["expected"]
        checks = [
            bookable(s) == exp["appointment_bookable_reference"],
            money_result(s) == exp["finance_reference"],
            exp["send_real_messages"] is False,
            exp["external_write_allowed"] is False,
            case["semantic_signature"] == digest({"area": case["area"], "state": s}),
            s["duration_min"] in DURATIONS,
        ]
        if s["target_tenant"] != "self":
            checks.append(exp["cross_tenant_access_denied"] is True)
        if not all(checks):
            failures.append({"scenario_id": case["scenario_id"], "area": case["area"],
                             "failed_checks": [i for i, ok in enumerate(checks) if not ok]})
        by_area[case["area"]] += 1
        tested += 1
        elapsed_ms.append((time.perf_counter() - t0) * 1000)
    ordered = sorted(elapsed_ms)

    def percentile(p):
        if not ordered:
            return None
        return round(ordered[min(len(ordered)-1, int((len(ordered)-1)*p))], 5)
    receipt = {
        "kind": "synthetic_reference_model", "note": "NOT Orion application, AI, DB or E2E",
        "executed_deterministic_reference_cases": tested,
        "passed_reference_cases": tested - len(failures),
        "failed_reference_cases": len(failures),
        "orion_app_tests_executed": 0, "ai_conversations_executed": 0,
        "postgres_integration_tests_executed": 0, "end_to_end_tests_executed": 0,
        "by_area": dict(by_area), "latency_reference_ms": {
            "p50": percentile(.50), "p95": percentile(.95), "p99": percentile(.99),
        },
        "wall_seconds": round(time.perf_counter() - started, 3),
        "failures": failures[:100],
    }
    (out / "reference_results.json").write_text(json.dumps(receipt, indent=2),
                                                encoding="utf-8")
    make_report(out, json.loads((out / "manifest.json").read_text(encoding="utf-8")),
                receipt)
    return receipt


def make_report(out, manifest, results=None):
    """Static, offline HTML dashboard. Empty evaluation categories remain zero."""
    results = results or {}
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" %
        (html.escape(k), format(v, ","), format(results.get("by_area", {}).get(k, 0), ","))
        for k, v in manifest["scenario_quotas"].items()
    )
    labels = [
        ("Cenarios gerados", manifest["generated_scenarios"]),
        ("Assinaturas semanticas distintas", manifest["unique_semantic_signatures"]),
        ("Jornadas geradas", manifest["generated_journeys"]),
        ("Empresas ficticias", manifest["synthetic_companies"]),
        ("Testes do modelo de referencia", results.get("executed_deterministic_reference_cases", 0)),
        ("Execucoes reais de IA", results.get("ai_conversations_executed", 0)),
        ("Integracoes PostgreSQL reais", results.get("postgres_integration_tests_executed", 0)),
        ("Ponta a ponta real", results.get("end_to_end_tests_executed", 0)),
    ]
    cards = "".join("<article><strong>%s</strong><span>%s</span></article>" %
                    (html.escape(str(v)), html.escape(k)) for k, v in labels)
    body = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orion Simulation & Reliability Lab</title>
<style>body{font:15px system-ui;margin:0;background:#0b1220;color:#e4ecf9}
main{max-width:1100px;margin:auto;padding:36px 20px}h1{font-size:30px}
p{line-height:1.6;color:#b5c4db}.grid{display:grid;grid-template-columns:
repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:22px 0}
article{background:#182338;padding:20px;border-radius:14px;
border:1px solid #2b3c59}strong{display:block;font-size:30px}
span{display:block;color:#a9bdd7;margin-top:6px}table{border-collapse:
collapse;width:100%;background:#182338;border-radius:12px;overflow:hidden}
td,th{padding:12px;text-align:left;border-bottom:1px solid #2b3c59}
th{color:#a9bdd7}code{color:#91caff}</style></head><body><main>
<h1>Orion Simulation & Reliability Lab</h1>
<p>Relatorio de dados sinteticos e verificacoes locais. Gerar casos nao significa
executar o aplicativo Orion, conversar com um modelo, testar PostgreSQL ou
executar navegacao real. Nenhum contato real recebe mensagens deste laboratorio.</p>
<section class="grid">""" + cards + """</section>
<h2>Cobertura do corpus</h2><table><thead><tr><th>Area</th><th>Gerados</th>
<th>Executados no modelo de referencia</th></tr></thead><tbody>""" + rows + """</tbody></table>
<p>Resultados brutos: <code>manifest.json</code> e
<code>reference_results.json</code>. Falhas reais da aplicacao requerem
execucao independente e evidencias verificaveis.</p></main></body></html>"""
    (out / "report.html").write_text(body, encoding="utf-8")


def cli():
    parser = argparse.ArgumentParser(description="Offline synthetic Orion reliability lab")
    parser.add_argument("command", choices=("generate", "reference", "all", "verify"))
    parser.add_argument("--out", default="simulation_lab/artifacts")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--max-reference", type=int, default=100000)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if args.command in ("generate", "all"):
        print(json.dumps({"generation": generate(out, seed=args.seed)}, ensure_ascii=False))
    if args.command in ("reference", "all"):
        print(json.dumps({"reference": run_reference(out, args.max_reference)},
                         ensure_ascii=False))
    if args.command == "verify":
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        counts = Counter()
        signatures = set()
        for item in read_jsonl_gz(out / "scenarios.jsonl.gz"):
            counts[item["area"]] += 1
            assert item["semantic_signature"] == digest({
                "area": item["area"], "state": item["state"]
            })
            signatures.add(item["semantic_signature"])
        journeys = sum(1 for _ in read_jsonl_gz(out / "journeys.jsonl.gz"))
        assert dict(counts) == QUOTAS and len(signatures) == TOTAL
        assert journeys >= 10000
        assert manifest["generated_scenarios"] == TOTAL
        for name, meta in manifest["files"].items():
            assert hashlib.sha256((out / name).read_bytes()).hexdigest() == meta["sha256"]
        print(json.dumps({"verified": True, "counts": dict(counts),
                          "unique_semantic_signatures": len(signatures),
                          "journeys": journeys}))
if __name__ == "__main__":
    cli()