"""Import reference cases, keeping customer input separate from test oracles.

This tool has no model, provider, database or message transport. Reference notes
never create domain state. Full journey execution requires a separately reviewed
fixture and a real state/side-effect oracle, not text similarity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

SOURCE_SHA256='4f3f00960e3d385788be1f3a57d4cc7e7e38df0ac6d8e926a815eaa1f60298ae'


def blocks(value):return re.findall(r'```(?:text)?\s*\n(.*?)\n```',value,re.S)


def import_case(record,annex):
    source=record['source'];events=[];oracle=[];notes=[]
    if annex=='C':
        sections=re.split(r'(?m)^### V2-\d{3} · Evento ',source)[1:]
        for section in sections:
            heading=section.splitlines()[0]
            match=re.match(r'(\d+) · t=([\d.]+)s · ([^/]+) / (.+)',heading)
            if not match:raise ValueError('Unrecognized event heading: '+record['id'])
            bodies=blocks(section)
            if len(bodies)!=1:raise ValueError('One complete event body required')
            event={'order':int(match[1]),'at_seconds':float(match[2]),'role':match[3].strip(),
                'kind':match[4].strip(),'body':bodies[0]}
            if event['role']=='cliente':events.append(event)
            elif event['role']=='orion':oracle.append(event)
            else:notes.append(event)
    elif annex=='B':
        sections=re.split(r'(?m)^### DIA-\d{2} · Turno ',source)[1:]
        for order,section in enumerate(sections,1):
            parts=re.split(r'(?m)^Resposta esperada:\s*$',section,maxsplit=1)
            incoming=blocks(parts[0]);outgoing=blocks(parts[1]) if len(parts)==2 else []
            if len(incoming)!=1:raise ValueError('One complete customer turn required')
            events.append({'order':order,'role':'cliente','kind':'text','body':incoming[0]})
            oracle.append({'order':order,'body':outgoing[0] if outgoing else '',
                'reference_conditions':record['fields']})
            notes+=re.findall(r'(?m)^(?:Nota|Ajustes da fixture|Fixture): (.*)$',section)
        # Resolve the contradictory reference by requesting the missing interval.
        # The fixed fixture clock makes Friday 23/10, but supplies no valid hours.
        if record['id']=='DIA-40':
            oracle=[{'order':1,'body':'Você pediu sexta-feira, 23/10/2026, à tarde. Qual intervalo deseja bloquear? Nenhum bloqueio foi criado.',
                'effect':'clarify_missing_interval'},
                {'order':2,'body':'Preciso do intervalo e da revisão dos conflitos antes de preparar a confirmação. Nenhum bloqueio foi criado.',
                 'effect':'no_write_without_interval_and_review'}]
    elif annex=='F':
        body=blocks(source)
        if len(body)!=1:raise ValueError('One language input required')
        events=[{'order':1,'role':'cliente','kind':'text','body':body[0]}]
        oracle=[{'expected_class':record['fields'].get('Classe esperada'),
            'required_context':record['fields'].get('Contexto necessário')}]
    return {'id':record['id'],'title':record['title'],'annex':annex,
        'source_lines':[record['start_line'],record['end_line']],
        'source_fragment_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'customer_events':events,'reference_oracle':oracle,'fixture_notes':notes,
        'context_description':record['fields'].get('Estado inicial') or record['fields'].get('Contexto necessário'),
        'state_materialized':False,'dialogue_acceptance':'not_executed',
        'authority':'reference_only_never_message_authority'}


def customer_payloads(case):
    """No expected answer, backend note, admin role or effect enters this path."""
    return [{'kind':event['kind'],'text':event['body'],'order':event['order']}
        for event in case['customer_events'] if event['role']=='cliente']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--index',type=Path,required=True,help='Audited section index; checked against the exact source')
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'tests/fixtures/communication')
    args=parser.parse_args();raw=args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA256:raise ValueError('Source changed; review provenance before regeneration')
    source=raw.decode('utf-8-sig');index=json.loads(args.index.read_text(encoding='utf-8-sig'))
    cases=[]
    for annex,count in [('B',50),('C',160),('F',128)]:
        if len(index[annex])!=count:raise ValueError('Unexpected reference count')
        for record in index[annex]:
            if record['source'].strip() not in source:raise ValueError('Indexed fragment not in source')
            cases.append(import_case(record,annex))
    result={'schema_version':1,'source_sha256':SOURCE_SHA256,'source_lines':len(source.splitlines()),
        'counts':{'B':50,'C':160,'F':128},'cases':cases,
        'oracle_patches':[{'id':'DIA-40','original_source_preserved':True,'source_line':11336,
            'reason':'24/10/2026 is Saturday; the customer asked Friday afternoon with no exact interval.',
            'resolution':'Fixed fixture Friday 23/10/2026; ask for hours instead of inventing 12–13 or other availability.'}],
        'limits':['No model/ASR/media/native WhatsApp executed by import.',
            'Fixture notes are references; domain state must be seeded by a trusted test harness.',
            'Expected prose is never a domain or delivery receipt.']}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'references.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cases':len(cases),'source_sha256':SOURCE_SHA256,'dialogue_acceptance':'not_executed'}))


if __name__=='__main__':main()
