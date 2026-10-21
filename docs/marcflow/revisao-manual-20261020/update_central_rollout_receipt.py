"""Reconcile supplied phase receipts; never access or modify production."""
from pathlib import Path
from datetime import datetime, timezone
import json

HERE = Path(__file__).resolve().parent
receipt_path = HERE / 'publication-receipt.json'
receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
receipt['updated_at_utc'] = datetime.now(timezone.utc).isoformat()

def upsert(name, value):
    matches = [i for i, item in enumerate(receipt['deployments']) if item['name'] == name]
    assert len(matches) <= 1
    value = {'name': name, **value}
    if matches:
        receipt['deployments'][matches[0]] = value
    else:
        receipt['deployments'].append(value)

e_path = HERE / 'railway-native-central-E-results.json'
if e_path.exists():
    e = json.loads(e_path.read_text(encoding='utf-8-sig'))
    assert e['status'] == 'SUCCESS'
    adoption = e['adoption']
    assert adoption['status'] == 'complete' and adoption['phase'] == 'activate'
    assert adoption['authority_adopted'] is True and adoption['registered'] is True
    assert all(adoption[key] is True for key in ['restore_verified', 'rehearsal_verified', 'session_preserved'])
    upsert('Adoção da autoridade QR(E)', {
        'status': 'succeeded', 'commit': e['deployment_reported_commit'],
        'configured_source_commit': e['configured_source_commit'],
        'summary': f"{e['deployment_id']} SUCCESS; activate complete/authority_adopted=true, backup cifrado de {adoption['backup_bytes']} bytes, leitura de volta, restauração, ensaio e sessão preservada. Verificação privada de prontidão passou antes do operador; pausas e backlog protegidos. Controle/envio globais ainda em0 nesta etapa.",
        'evidence': [{'kind': 'actual_operator_and_cleanup', 'path': e_path.name}, {'kind': 'public_readonly_health', 'path': 'public-after-native-central-E.json'}]
    })
    central_state = 'Admin e IA da demonstração aplicados; sessão QR registrada, adaptador pronto e autoridade persistente adotada. Habilitação global do controle/envio humano(F) ainda pendente. Sessão e dados preservados; sem campanhas ou mensagens de teste.'
    pending = ['PendingF: confirmar limpezaE/hooknormal, saúde/sessão e habilitar controle/envio somente API/worker; manter Meta, campanhas e sondagens desligadas.', 'Pending: novo login do próprio admin e conferência da tela somente de leitura, sem mensagem real; sessão antiga continua válida.']
else:
    upsert('Adoção da autoridade QR(E)', {
        'status': 'in_progress',
        'commit': '168872acffc015b4aaf51c4d96fda46720d66ba5',
        'configured_source_commit': 'a85da1c9ade64a5fc21bc9927efef727447ba83f',
        'summary': 'Patch e98379ba aplicado no painel; deployment5501a2cf observado BUILDING. Sem recibo de adoção até esta atualização. A preparação e37testes isolados da guarda não comprovam conclusão em produção.',
        'evidence': [{'kind': 'reviewed_patch', 'path': 'native-central-E-staged.json'}, {'kind': 'isolated_guard', 'path': 'native-central-E-guard-evidence.json'}]
    })
    central_state = 'Admin e IA da demonstração aplicados; sessão QR registrada (C) e adaptador (D) publicado e pronto, com os 96 arquivos de autenticação e o volume originais. Adoção E em publicação; habilitação F pendente. Controle e envio globais desligados; sem campanhas ou mensagens de teste.'
    pending = ['PendingE: verificar recibo real completo, backup/restauração/ensaio, sessão, pausas e backlog; preparação e build não provam autoridade adotada.', 'PendingF: apósEcomplete, limpar opçõesE/restaurar hooknormal, revalidar saúde e liberar controle/envio somente API/worker, mantendo Meta/campanhas/sondagens desligados.', 'Pending: novo login do próprio admin e conferência da tela somente de leitura; sessão antiga continua válida.']

f_path = HERE / 'railway-native-central-F-results.json'
if f_path.exists():
    f = json.loads(f_path.read_text(encoding='utf-8-sig'))
    assert f['status'] == 'SUCCESS' and f['qr_session_preserved'] is True
    assert all(f[name]['status'] == 'SUCCESS' for name in ('api', 'worker'))
    for name in ('api', 'worker'):
        flags = f['effective_flags'][name]
        assert all(flags[key] == '1' for key in ('INBOX_CONTROL_ENABLED', 'INBOX_SEND_ENABLED', 'QR_AI_ENABLED', 'QR_CHANNELS_ENABLED', 'QR_LEGACY_CENTRAL_ENABLED', 'INBOX_TOOLS_ENABLED'))
        assert all(flags[key] == '0' for key in ('META_LIVE_SEND', 'MARKETING_EXECUTION_ENABLED', 'MARKETING_RECONCILIATION_ENABLED'))
    upsert('Habilitação humana(F)', {
        'status': 'succeeded', 'commit': f['api']['deployment_reported_commit'],
        'summary': f"API {f['api']['deployment_id']} e worker {f['worker']['deployment_id']} SUCCESS; controle e fila de envio humano ligados, IA/canais/ferramentas preservados. Meta/campanhas/sondagens desligados. QR, volume e sessão preservados; nenhuma mensagem real de teste.",
        'evidence': [{'kind': 'actual_runtime_configuration', 'path': f_path.name}, {'kind': 'public_readonly_health', 'path': 'public-after-native-central-F.json'}]
    })
    central_state = 'Central humana QR habilitada na API e worker, com autoridade persistente e sessão preservada. IA e ferramentas ligadas; Meta, campanhas e sondagens desligados. Novo login e conferência do admin ainda pendentes; nenhuma mensagem real de teste.'
    pending = ['Pendente: novo login do próprio admin e conferência da tela somente de leitura, sem mensagem real; sessão antiga continua válida.', 'Homologação de entrega/resposta real e demais provedores permanece fora dos testes executados.']
else:
    upsert('Habilitação humana(F)', {
        'status': 'in_progress', 'commit': '168872acffc015b4aaf51c4d96fda46720d66ba5',
        'summary': 'Usuário confirmou aplicação dos7campos do patch be4322e9. API47000175 e worker0bff9fba observados BUILDING; sem recibo de runtime habilitado até esta atualização. QR/volumes não fazem parte do patch.',
        'evidence': [{'kind': 'reviewed_patch_after_E', 'path': 'native-central-F-staged.json'}, {'kind': 'isolated_safe_logging', 'path': 'native-central-F-review-and-masking-test.json'}]
    })

login_path = HERE / 'native-admin-fresh-login-readonly.json'
if login_path.exists():
    login = json.loads(login_path.read_text(encoding='utf-8-sig'))
    assert f_path.exists() and login['status'] == 'passed'
    assert login['fresh_login_observed'] is True and login['no_control_business_message_mutations'] is True
    central_state = 'Central humana QR habilitada na API e worker; novo acesso do próprio admin conferido em leitura. Sessão, dados e IA preservados. Meta/campanhas/sondagens desligados; nenhuma mensagem real de teste. Entrega e resposta reais não foram homologadas.'
    pending = ['Homologação de entrega/resposta real e demais provedores permanece fora dos testes executados.']
    receipt['external_acceptance']['fresh_admin_login'] = {'status': 'passed_readonly', 'evidence': login_path.name, 'message_send_test': False}
    receipt['validation']['results']['native_admin_live_readonly_UI'] = {
        'status': 'passed', 'scope': 'Novo login feito pelo usuário; central, canal, histórico, permissões e filtros conferidos em leitura, sem ações de negócio, controle, marcação de leitura ou envio.',
        'old_admin_session_reloaded_still_authenticated': login['prior_admin_session_reload']['authenticated_admin_page_loaded'],
        'console_errors': login['checks']['console_errors'],
        'not_full20_browser_journeys_or_provider_homologation': True,
        'evidence': [login_path.name, *login['screenshots']]
    }

receipt['publication_status'] = central_state
receipt['ci']['summary'] = 'Runtime a85da1c qualificado: backend com PostgreSQL 16/Redis, 5.074 aprovados e 1 ignorado; Docker Compose, 4.909 aprovados e 166 ignorados, em grupos sobrepostos. QR: 446; DOM: 329; ASR: 4 e smoke sintético WAV/OGG; mídia: 10. Pip check e 104 correspondências dos 52 pins em dois ambientes confirmados. Testes da CI não incluem login ou envios reais; a conferência real de acesso possui recibo separado.'
ci_168 = json.loads((HERE / 'ci-168872a-final-evidence.json').read_text(encoding='utf-8-sig'))
assert ci_168['ci_head_sha'] == '168872acffc015b4aaf51c4d96fda46720d66ba5'
assert len(ci_168['runs']) == 2 and all(run['conclusion'] == 'success' for run in ci_168['runs'])
receipt['ci']['equivalent_reported_commit_qualification'] = {
    'status': 'passed', 'commit': ci_168['ci_head_sha'],
    'summary': 'Commit de documentação168 também passou em sua própria CI; fontes de runtime idênticas a a85. Contagens preservadas separadamente, sem soma entre rodadas ou grupos sobrepostos.',
    'evidence': [{'kind': 'actual_job_logs_and_counts', 'path': 'ci-168872a-final-evidence.json'}, {'kind': 'exact_pins', 'path': 'ci-168872a-constraints-proof.json'}]
}
receipt['external_acceptance']['central_activation'] = central_state
receipt['remaining_acceptance']['central_activation'] = pending
receipt['remaining_acceptance']['central_registration'] = [
    'Concluído: próprio admin persistido, propriedade/IA somente da demonstração e auditoria; backup do setup de476701bytes verificado.',
    'Concluído: registroC e adaptadorD com volume, autenticação e mesma sessão preservados; nenhum novo e-mail, usuário pessoal ou senha criado.'
]
if f_path.exists():
    receipt['remaining_acceptance']['central_registration'].append('Concluído: adoçãoE com backup de477386bytes/restauração/ensaio, habilitaçãoF API/worker e configuração efetiva conferida; Meta/campanhas/sondagens desligados.')
receipt['validation']['summary'] = 'Testes locais, CI, deployments e consulta real do administrador têm recibos separados. A conferência de UI é somente de leitura; grupos e rodadas sobrepostos não são somados nem representam homologação de envio real.'
for evidence in [
    {'kind': 'runtime_ci', 'path': 'ci-a85da1c-final-evidence.json'},
    {'kind': 'reported_commit_ci', 'path': 'ci-168872a-final-evidence.json'},
    {'kind': 'live_readonly_admin', 'path': 'native-admin-fresh-login-readonly.json'},
    {'kind': 'live_readonly_screenshot', 'path': 'native-admin-central-readonly-top.png'}
]:
    if (HERE / evidence['path']).is_file() and evidence not in receipt['validation']['evidence']:
        receipt['validation']['evidence'].append(evidence)
receipt['validation']['results']['legacy_central_E_preflight_guard'] = {
    'passed': 37, 'failures': 0, 'scope': 'Guarda GET e ordem do hook com HTTP/subprocess/versões substituídos; rede bloqueada. Não são testes do runtime real nem adoção em produção.',
    'evidence': ['native-central-E-guard-evidence.json', 'native-central-E-guard.xml', 'native-central-E-guard-network.json']
}
receipt['validation']['results']['legacy_central_F_safe_logging'] = {
    'passed': 5, 'failures': 0, 'scope': 'Somente três instruções de log, com variáveis fictícias e importações restritas. Não executa o predeploy completo, binding, esquema, worker ou produção.',
    'evidence': ['native-central-F-review-and-masking-test.json', 'native-central-F-masking-isolated.log']
}
receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'publication_status': central_state, 'deployments': len(receipt['deployments'])}, ensure_ascii=False))
