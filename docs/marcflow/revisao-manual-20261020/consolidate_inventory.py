"""Validate and consolidate manually reviewed records without refreshing evidence.

Read-only against the repository. A changed source hash is an error, never an
invitation to silently bless new code. Different reviewer schemas are preserved
verbatim under source_record in addition to the normalized index.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(r'C:\Users\kayky\Documents\Playground\orion-marcflow-20261020').resolve()
OUT = Path(__file__).resolve().parent
ORIGINAL = ROOT.parent / 'orion-comunicacao-auditoria-20261020' / 'output-candidates.json'
MANUAL = ['manual-api.json', 'manual-js.json', 'manual-emit.json']
FINDINGS = ['findings-api.json', 'findings-js.json', 'findings-emit.json']


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def relative_file(value):
    path = Path(value)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    return path, path.relative_to(ROOT).as_posix()


def normalize_tests(values, source_file):
    result = []
    for value in values or []:
        item = {'path': value} if isinstance(value, str) else dict(value)
        # A source pointer does not assert that the test was run or passed.
        item['reference_status'] = 'source_reference_only'
        item['execution_evidence'] = 'consult separate validation logs/JUnit; not inferred from this pointer'
        item['review_source'] = source_file
        result.append(item)
    return result


def canonical_finding_id(value):
    return value.upper()


def merge_findings():
    grouped = defaultdict(list)
    metadata = {}
    for filename in FINDINGS:
        payload = load(OUT / filename)
        rows = payload if isinstance(payload, list) else payload['findings']
        metadata[filename] = ({'sha256': sha(OUT / filename)} if isinstance(payload, list) else
                              {'sha256': sha(OUT / filename), 'source_metadata': {k:v for k,v in payload.items() if k!='findings'}})
        for row in rows:
            grouped[canonical_finding_id(row['id'])].append({'source': filename, 'record': row})
    result = []
    for identity, observations in sorted(grouped.items()):
        # Only explicit, attached execution evidence can establish tested status.
        tested = [o for o in observations if o['record'].get('status') in
                  {'fixed_tested_isolated', 'fixed_source_and_isolated_unit_regressions'}]
        primary = tested[0] if tested else observations[0]
        indexes = sorted({identifier for o in observations for identifier in o['record'].get('inventory_ids', [])})
        result.append({
            'id': identity,
            'aliases': sorted({o['record']['id'] for o in observations}),
            'title': primary['record']['title'],
            'status': 'fixed_tested_isolated' if tested else 'fixed_source_review_runtime_validation_not_attested_by_reviewer',
            'status_scope': 'local/source or isolated tests only; no live/provider homology or deployment inferred',
            'inventory_ids': indexes,
            'origins': [o['source'] for o in observations],
            'observations': observations,
            'superseded_status_notes': [
                {'source': o['source'], 'status_at_that_review': o['record'].get('status', o['record'].get('status_at_handoff')),
                 'reason': 'same canonical finding; later attached isolated regression evidence supersedes pending-test wording'}
                for o in observations if tested and o not in tested],
        })
    return result, metadata


def run():
    original = load(ORIGINAL)
    errors, stale, by_id, reviewers, supplemental = [], [], {}, {}, []
    source_cache = {}
    original_sha = sha(ORIGINAL)

    def checked_source(value, expected, provenance):
        try:
            path, relative = relative_file(value)
        except (ValueError, TypeError):
            errors.append({'type':'source_outside_repository','source':provenance,'file':value})
            return None, None
        if not path.is_file():
            errors.append({'type':'source_missing','source':provenance,'file':relative})
            return None, None
        if not isinstance(expected, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
            errors.append({'type':'source_hash_invalid','source':provenance,'file':relative})
            return None, None
        if relative not in source_cache:
            content = path.read_bytes()
            source_cache[relative] = {'sha256':hashlib.sha256(content).hexdigest(),
                                      'lines':len(content.decode('utf-8-sig').splitlines())}
        current = source_cache[relative]
        if current['sha256'] != expected.lower():
            stale.append({'source':provenance,'file':relative,'reviewed_sha256':expected.lower(),
                          'current_sha256':current['sha256'],'action':'manual reread required; record not refreshed'})
        return relative, current

    for filename in MANUAL:
        payload = load(OUT / filename)
        records = payload if isinstance(payload, list) else payload['records']
        metadata = {} if isinstance(payload, list) else {k:v for k,v in payload.items() if k!='records'}
        reviewers[filename] = {'records':len(records),'sha256':sha(OUT / filename),'metadata':metadata}
        if metadata.get('inventory_sha256', original_sha) != original_sha:
            errors.append({'type':'original_inventory_hash_mismatch','source':filename})
        for file, digest in metadata.get('reviewed_dependency_hashes', {}).items():
            checked_source(file, digest, filename + ':reviewed_dependency_hashes')
        for record in records:
            identity = record.get('id', '')
            match = re.fullmatch(r'INV(\d{4})', identity)
            if not match or not 1 <= int(match[1]) <= len(original):
                errors.append({'type':'invalid_inventory_id','source':filename,'id':identity})
                continue
            if identity in by_id:
                errors.append({'type':'duplicate_inventory_id','source':filename,'id':identity})
                continue
            position = int(match[1]); baseline = original[position-1]
            if record.get('original_index', position) != position:
                errors.append({'type':'original_position_mismatch','source':filename,'id':identity})
            supplied = record.get('original')
            if supplied is not None and supplied != baseline:
                errors.append({'type':'original_record_mismatch','source':filename,'id':identity})
            for key, original_key in [('original_file','file'),('original_line','line'),
                                      ('original_function','function'),('original_kind','kind')]:
                if key in record and record[key] != baseline[original_key]:
                    errors.append({'type':'original_field_mismatch','source':filename,'id':identity,'field':key})
            for key in ['original_expression']:
                if key in record and re.sub(r'\s+', '', record[key]) != re.sub(r'\s+', '', baseline['excerpt']):
                    errors.append({'type':'original_expression_mismatch','source':filename,'id':identity})
            required = ['current_function','current_file','rationale','actual_sink','disposition','review_method']
            for key in required:
                if not isinstance(record.get(key), str) or not record[key].strip():
                    errors.append({'type':'required_review_field_missing','source':filename,'id':identity,'field':key})
            if record.get('reviewed') is not True:
                errors.append({'type':'review_not_complete','source':filename,'id':identity})
            relative, current = checked_source(record.get('current_file'), record.get('source_sha256'), filename + ':' + identity)
            span, line = record.get('current_range'), record.get('current_line')
            valid_span = (isinstance(span, dict) and type(span.get('start')) is int and type(span.get('end')) is int
                          and type(line) is int and 1 <= span['start'] <= line <= span['end'])
            if not valid_span or current is not None and span['end'] > current['lines']:
                errors.append({'type':'current_location_invalid','source':filename,'id':identity,'line':line,'range':span})
            normalized = {
                'id':identity,'original_index':position,'original':baseline,'reviewed':record.get('reviewed') is True,
                'review_source':filename,'review_method':record.get('review_method'),
                'current_file':relative,'current_function':record.get('current_function'),
                'current_line':line,'current_range':span,'source_sha256':record.get('source_sha256'),
                'disposition':record.get('disposition'),'rationale':record.get('rationale'),'actual_sink':record.get('actual_sink'),
                'relevant_tests':normalize_tests(record.get('relevant_tests'), filename),
                'findings_ids':sorted({canonical_finding_id(v) for v in record.get('findings_ids', [])}),
                'source_record':record,
                'execution_claim':'manual source review; test references do not imply execution; consult validation artifacts',
            }
            by_id[identity] = normalized

    if (OUT / 'api-followup-review.json').is_file():
        followup = load(OUT / 'api-followup-review.json')
        supplemental.append({'source':'api-followup-review.json','sha256':sha(OUT / 'api-followup-review.json'),'review':followup})
        for item in followup.get('files', []):
            checked_source(item['file'], item['source_sha256'], 'api-followup-review.json')

    expected = {f'INV{i:04d}' for i in range(1,len(original)+1)}
    missing = sorted(expected-set(by_id))
    if missing:
        errors.append({'type':'missing_inventory_ids','ids':missing})
    findings, findings_metadata = merge_findings()
    known_findings = {item['id'] for item in findings}
    for row in by_id.values():
        for value in row['findings_ids']:
            if value not in known_findings:
                errors.append({'type':'unknown_finding_reference','id':row['id'],'finding':value})
    # Deduplicate repeated stale reports while preserving all reviewer origins.
    stale_by = {}
    for item in stale:
        key=(item['file'], item['reviewed_sha256'], item['current_sha256'])
        if key not in stale_by:
            stale_by[key]={**item,'sources':[]};stale_by[key].pop('source')
        stale_by[key]['sources'].append(item['source'])
    stale = list(stale_by.values())
    summary = {
        'created_at_utc':datetime.now(timezone.utc).isoformat(),'repository':str(ROOT),
        'original_inventory':str(ORIGINAL),'original_inventory_sha256':original_sha,
        'validation_status':'blocked_manual_reread_required' if stale else 'blocked_invalid_evidence' if errors else 'valid_current_source_snapshot',
        'expected_records':len(original),'reviewed_unique_records':len(by_id),'all_records_reviewed':len(by_id)==len(original) and all(x['reviewed'] for x in by_id.values()),
        'missing_ids':missing,'duplicate_id_errors':sum(e['type']=='duplicate_inventory_id' for e in errors),
        'current_source_files':len(source_cache),'review_sources':reviewers,
        'by_original_kind':dict(Counter(row['original']['kind'] for row in by_id.values())),
        'by_disposition':dict(Counter(row['disposition'] for row in by_id.values())),
        'findings_raw_observations':sum(len(f['observations']) for f in findings),'findings_unique':len(findings),
        'findings_by_status':dict(Counter(f['status'] for f in findings)),
        'test_reference_semantics':'Pointers only. Actual test executions are separate logs/JUnit; no distinct-pass total is inferred or summed here.',
        'live_acceptance':'not executed: no provider/model/ASR/native-message/Railway deployment acceptance inferred from manual review',
        'stale_sources':stale,'errors':errors,
    }
    dump('manual-inventory-validation.json', {'summary':summary,'stale_sources':stale,'errors':errors})
    dump('manual-inventory-summary.json', summary)
    if errors or stale:
        print(json.dumps({'status':summary['validation_status'],'records':len(by_id),'errors':len(errors),'stale_files':len(stale)}, ensure_ascii=False))
        return 2
    result = {
        'schema_version':1,'summary':summary,'records':[by_id[key] for key in sorted(by_id)],
        'findings':findings,'findings_source_metadata':findings_metadata,'supplemental_reviews':supplemental,
        'evidence_preservation':'Each source record and finding observation is preserved verbatim. No source hash was refreshed automatically.',
    }
    dump('manual-inventory.json', result)
    print(json.dumps({'status':summary['validation_status'],'records':len(by_id),'findings_unique':len(findings),'findings_raw':summary['findings_raw_observations']}, ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(run())
