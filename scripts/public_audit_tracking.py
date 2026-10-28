"""Keep the audit IDs and the individually verified evidence in one ledger."""
import argparse
import json
from pathlib import Path

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('id');parser.add_argument('state',choices=['pendente','em análise','em correção','em validação','validado'])
    parser.add_argument('--note',default='');parser.add_argument('--tests',default='')
    args=parser.parse_args()
    path=Path(__file__).resolve().parents[1]/'docs/operations/public-audit-20261028.json'
    data=json.loads(path.read_text(encoding='utf-8'))
    item=next(x for x in data['items'] if x['id']==args.id)
    item['state']=args.state
    if args.note:item['note']=args.note
    if args.tests:item['tests']=args.tests
    if args.state=='validado':
        item['evidence']=[f'C:/Users/kayky/Documents/Playground/orion-public-fixes-20261028/{args.id}.json']
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(args.id,args.state)
