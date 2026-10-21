"""Emit actual pure-plan observations separately from reference expectations."""
from datetime import date
import argparse,json
from pathlib import Path
from uuid import UUID
from app.qr_understanding import build_plan
from scripts.communication_corpus import customer_payloads


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    corpus=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/communication/references.json').read_text(encoding='utf-8'))
    catalog=[{'id':UUID(int=1),'name':'Corte de cabelo','price_cents':3500},{'id':UUID(int=2),'name':'Barba','price_cents':2500}]
    observations=[]
    for case in corpus['cases']:
        if case['annex']!='F':continue
        body=customer_payloads(case)[0]['text']
        plan=build_plan(message=body,catalog=catalog,reference_date=date(2026,10,20),context={})
        observations.append({'id':case['id'],'input':body,'proposal':plan.model_dump(mode='json'),
            'reference_expected':case['reference_oracle'],'trusted_context_materialized':False,
            'semantic_acceptance':'not_evaluated','effects':[]})
    args.output.write_text(json.dumps({'source_sha256':corpus['source_sha256'],'layer':'pure_planner_probe',
        'count':len(observations),'real_model_calls':0,'real_messages':0,'observations':observations},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'pure_planner_probes':len(observations),'dialogue_acceptance':'not_executed'}))


if __name__=='__main__':main()
