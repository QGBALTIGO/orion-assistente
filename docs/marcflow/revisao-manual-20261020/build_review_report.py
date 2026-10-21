"""Build the offline manual-review report without revising/reattesting sources.

Usage: python build_review_report.py [--receipt publication-receipt.json]

Optional receipt (data supplied by the operator; never fetched by this helper):
{"schema_version":1,"updated_at_utc":"2026-10-20T22:00:00Z",
 "publication_status":"Implementado/revisado; publicação em preparação",
 "ci":{"status":"pending","summary":"Aguardando CI","evidence":[]},
 "validation":{"summary":"Atualização dos testes, fornecida pelo executor",
   "results":{"postgres_broad":"pending"},"evidence":[{"path":"tests.log"}]},
 "deployments":[{"name":"Railway API","status":"pending",
   "summary":"Aguardando publicação","commit":"<40 hexadecimal characters>",
   "evidence":[{"kind":"receipt","path":"railway-receipt.json"}]}]}

Completion/failure claims need a nonempty evidence list and are always labelled
as reported by that receipt. This helper never inspects CI or Railway, updates
source hashes, runs application code, or infers deployment from local tests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import pathlib
import re

HERE=pathlib.Path(__file__).resolve().parent
EXPECTED=1247

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))

def load_receipt(path):
    if path is None:return None
    receipt=read(path)
    if not isinstance(receipt,dict) or receipt.get('schema_version')!=1:
        raise ValueError('Receipt requires schema_version=1 and an object.')
    if 'publication_status' in receipt and (not isinstance(receipt['publication_status'],str) or not receipt['publication_status'].strip()):
        raise ValueError('A supplied publication_status must be explicit nonempty text.')
    allowed={'pending','not_started','queued','in_progress','passed','succeeded','failed','cancelled','blocked','rolled_back'}
    validation=receipt.get('validation')
    if validation is not None and (not isinstance(validation,dict) or not isinstance(validation.get('evidence'),list) or not validation['evidence']):
        raise ValueError('A test update requires validation.evidence with explicit execution evidence.')
    deployments=receipt.get('deployments',[])
    if not isinstance(deployments,list):raise ValueError('Receipt deployments must be a list.')
    for item in ([receipt['ci']] if 'ci' in receipt else [])+deployments:
        if not isinstance(item,dict) or item.get('status') not in allowed:
            raise ValueError('Every CI/deployment receipt needs an explicit recognised status.')
        evidence=item.get('evidence',[])
        if not isinstance(evidence,list):raise ValueError('Receipt evidence must be a list.')
        if item['status'] in {'passed','succeeded','failed','cancelled','rolled_back'} and not evidence:
            raise ValueError('A terminal CI/deploy status needs a concrete evidence list; no inferred acceptance.')
        if 'commit' in item and not re.fullmatch('[a-fA-F0-9]{40}',item['commit']):
            raise ValueError('A receipt commit must be the complete 40-character commit.')
    return {'data':receipt,'path':path.name,'sha256':sha(path),
            'semantics':'Operator-supplied receipt, displayed verbatim; not independently checked by this report builder.'}

def compact_inventory(raw,input_path):
    summary=raw['summary'];records=raw['records']
    assert summary['all_records_reviewed'] is True and summary['validation_status']=='valid_current_source_snapshot'
    assert not summary.get('stale_sources') and not summary.get('errors')
    assert len(records)==EXPECTED
    assert [r['id'] for r in records]==[f'INV{i:04d}' for i in range(1,EXPECTED+1)]
    source_root=pathlib.Path(summary['repository']);file_hashes={};compact=[]
    for row in records:
        assert row['reviewed'] is True
        source=row['source_record'];original=row['original'];span=row['current_range']
        assert 1<=span['start']<=row['current_line']<=span['end']
        file=row['current_file'];hash_value=row['source_sha256']
        assert file_hashes.setdefault(file,hash_value)==hash_value
        compact.append({
            'id':row['id'],'original_index':row['original_index'],'reviewed':True,
            'original':{'file':original['file'],'line':original['line'],'function':original.get('function'),
                        'kind':original['kind'],'excerpt':original.get('excerpt','')[:280],
                        'excerpt_shortened':len(original.get('excerpt',''))>280},
            'review_source':row['review_source'],'review_method':row['review_method'],
            'current_file':file,'current_function':row['current_function'],
            'current_line':row['current_line'],'current_range':span,
            'reviewed_context_range':source.get('reviewed_context_range'),
            'source_sha256':hash_value,'disposition':row['disposition'],
            'rationale':row['rationale'],'guards':source.get('current_control_conditions',[]),
            'guards_basis':'Explicit source conditions plus manual rationale' if source.get('current_control_conditions') else 'Guardas descritas na fundamentação manual; sem extração estrutural separada.',
            'routes':source.get('current_routes',[]),'function_calls':source.get('function_calls',[]),
            'actual_sink':row['actual_sink'],'relevant_tests':row['relevant_tests'],
            'findings_ids':row['findings_ids'],'execution_claim':row['execution_claim'],
        })
    # Verify the already-reviewed snapshot. Fail instead of silently replacing
    # hashes or claiming a review of any later source change.
    for file,expected in file_hashes.items():
        actual=sha(source_root/file)
        if actual!=expected:raise ValueError(f'Stale manual-review snapshot: {file}. Reconcile source review first.')
    dependency_hashes={}
    def dependency(file,value):
        assert isinstance(file,str) and re.fullmatch('[a-fA-F0-9]{64}',value)
        assert (source_root/file).resolve().is_relative_to(source_root.resolve())
        file=(source_root/file).resolve().relative_to(source_root.resolve()).as_posix()
        assert dependency_hashes.setdefault(file,value)==value
        assert file not in file_hashes or file_hashes[file]==value
    for review_source in summary.get('review_sources',{}).values():
        for file,value in review_source.get('metadata',{}).get('reviewed_dependency_hashes',{}).items():
            dependency(file,value)
    for supplement in raw.get('supplemental_reviews',[]):
        for entry in supplement.get('review',{}).get('files',[]):
            if entry.get('source_sha256'):
                dependency(entry['file'],entry['source_sha256'])
    for file,expected in dependency_hashes.items():
        if sha(source_root/file)!=expected:raise ValueError(f'Stale dependency-review snapshot: {file}. Reconcile source review first.')
    line_counts={file:len((source_root/file).read_text(encoding='utf-8').splitlines()) for file in file_hashes}
    for row in records:
        assert row['current_range']['end']<=line_counts[row['current_file']]
    finding_ids=[item['id'] for item in raw['findings']]
    assert all(isinstance(value,str) and value for value in finding_ids) and len(finding_ids)==len(set(finding_ids))
    observations=sum(len(item['observations']) for item in raw['findings'])
    assert summary['findings_unique']==len(finding_ids)
    assert summary['findings_raw_observations']==observations
    keep_summary=['created_at_utc','repository','original_inventory','original_inventory_sha256',
      'validation_status','expected_records','reviewed_unique_records','all_records_reviewed',
      'missing_ids','duplicate_id_errors','current_source_files','by_original_kind','by_disposition',
      'findings_raw_observations','findings_unique','findings_by_status','test_reference_semantics',
      'live_acceptance','stale_sources','errors']
    result={'schema_version':1,'kind':'complete_compact_manual_review_index',
        'raw_source':{'path':input_path.name,'sha256':sha(input_path)},
        'summary':{key:summary[key] for key in keep_summary if key in summary},
        'source_files':file_hashes,'dependency_files':dependency_hashes,'records':compact,'findings':raw['findings'],
        'supplemental_reviews':raw.get('supplemental_reviews',[]),
        'preservation':'All1247 conclusions, guards, sinks, test references and source hashes preserved. Duplicated source_record/current_statement omitted. Original excerpts limited to280characters; full frozen source remains in manual-inventory.json.'}
    for before,after in zip(records,compact):
        for key in ['id','current_file','current_function','current_line','current_range','source_sha256',
                    'disposition','rationale','actual_sink','relevant_tests','findings_ids','execution_claim']:
            assert before[key]==after[key],(before['id'],key)
    return result

STYLE=r'''
:root{font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:#17243b;background:#f4f7fb;--line:#d9e1ec;--muted:#4c5a71;--blue:#1754aa;--pale:#ecf2fb}
*{box-sizing:border-box}body{margin:0}main{max-width:1260px;margin:auto;padding:32px 22px 72px}a{color:var(--blue)}h1{font-size:clamp(1.8rem,4vw,2.7rem);line-height:1.18;margin:.5rem 0 1rem}h2{font-size:1.45rem;line-height:1.3;margin:0 0 1rem}h3{font-size:1.1rem;margin:.5rem 0}p{margin:.6rem 0}small,.muted{color:var(--muted)}.overline{font-size:.76rem;letter-spacing:.14em;font-weight:750;text-transform:uppercase;color:var(--blue)}header{margin-bottom:24px}.lead{max-width:850px;font-size:1.08rem}.status,.badge{display:inline-block;border-radius:30px;padding:5px 12px;background:var(--pale);font-size:.84rem;font-weight:650}.status{background:#e6f4ed;color:#12633f}.notice{border-left:5px solid #ba8c22;background:#fff2cf;padding:18px 20px;border-radius:10px;margin:20px 0}nav{display:flex;flex-wrap:wrap;gap:10px 20px;margin:22px 0}nav a{font-weight:600;text-underline-offset:4px}section{background:white;border:1px solid var(--line);border-radius:14px;padding:24px;margin:22px 0;scroll-margin-top:20px}.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}.fact{padding:18px;background:white;border:1px solid var(--line);border-radius:12px}.fact strong{display:block;font-size:1.85rem;letter-spacing:-.03em}.fact span{color:var(--muted);font-size:.88rem}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.91rem}th,td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}th{background:var(--pale);font-weight:650}table th:first-child{min-width:140px}table td:last-child{min-width:230px}.ok{color:#13734b}.pending{color:#8b6010}.warning{background:#fff8e6}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.subcard{background:#f8fafd;border:1px solid var(--line);border-radius:10px;padding:16px}.subcard h3{margin-top:0}.subcard pre{margin:10px 0 0}code,pre{overflow-wrap:anywhere;white-space:pre-wrap;word-break:break-word;font:12.5px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}pre{margin:8px 0;background:#f3f6fa;border:1px solid #e1e7ef;padding:12px;border-radius:8px;max-height:380px;overflow:auto}dl{margin:12px 0;display:grid;grid-template-columns:minmax(120px,180px) 1fr;gap:8px 14px}dt{font-weight:650;color:var(--muted)}dd{margin:0;min-width:0}.filters{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;padding:16px;background:#f8fafd;border:1px solid var(--line);border-radius:10px}label{display:block;font-size:.86rem;font-weight:650}input,select,button{font:inherit}input,select{display:block;margin-top:6px;padding:10px;width:100%;border:1px solid #aebdd0;border-radius:7px;background:white;color:#17243b}input:focus,select:focus,button:focus-visible,summary:focus-visible{outline:3px solid #80b3ef;outline-offset:2px}button{cursor:pointer;border:1px solid #9eb4cf;border-radius:7px;background:white;color:var(--blue);padding:8px 13px;font-weight:650}button:disabled{cursor:default;opacity:.45}.pager{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:16px 0;flex-wrap:wrap}.pager div{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.pager label{display:flex;gap:10px;align-items:center}.pager select{width:auto;margin:0;padding:6px}.result-count{font-size:.91rem;color:var(--muted);margin:12px 0}.inventory-item,.finding{border:1px solid var(--line);border-radius:10px;margin:12px 0;overflow:hidden}.inventory-item>summary,.finding>summary{padding:15px 17px;cursor:pointer;font-weight:650;background:#fbfcff}.inventory-item>summary{display:flex;gap:12px;align-items:flex-start;list-style:none}.inventory-item>summary::-webkit-details-marker{display:none}.inventory-item>summary:before{content:'+';color:var(--blue);font-size:1.1rem;line-height:1.3}.inventory-item[open]>summary:before{content:'−'}.id{color:var(--blue);font:700 .86rem/1.55 ui-monospace,monospace;white-space:nowrap}.item-title{min-width:0;overflow-wrap:anywhere}.item-title small{display:block;font-weight:450}.item-body{padding:0 20px 18px}.item-body h4{font-size:.9rem;margin:18px 0 5px}.item-body ul{padding-left:20px}.item-body li{margin:.3rem 0}.record-hash{font-size:11.5px}.finding>summary{display:block}.finding .badge{font-size:.72rem;margin-top:7px;font-weight:600}.inline-id{font-size:.78rem;margin:3px 4px 3px 0;padding:3px 8px}.empty{padding:28px;text-align:center;color:var(--muted);background:#f7f9fc;border-radius:8px}.raw-links{display:flex;flex-wrap:wrap;gap:12px}.footer{margin-top:24px;font-size:.85rem;color:var(--muted)}noscript{display:block;padding:20px;border:2px solid #ba8c22;background:#fff2cf}.tight{margin:0}.receipt-label{font-size:.8rem;color:#526479}.source-note{font-size:.88rem;max-width:960px}.finding-observation{padding:12px 0;border-top:1px solid #e5ebf3}.finding-observation:first-of-type{border-top:0}.finding-observation p{overflow-wrap:anywhere}
@media(max-width:760px){main{padding:20px 12px 48px}section{padding:17px}.facts{grid-template-columns:repeat(2,minmax(0,1fr))}.grid,.filters{grid-template-columns:1fr}dl{grid-template-columns:1fr;gap:3px}dd{margin-bottom:10px}.inventory-item>summary{padding:12px;gap:9px}.item-body{padding:0 13px 16px}pre{max-height:300px}.pager{align-items:flex-start}.fact{padding:14px}.fact strong{font-size:1.6rem}}
@media print{body{background:white}main{max-width:none;padding:0}nav,.filters,.pager,.raw-links{display:none}section{border-radius:0;padding:14px;border-color:#bbb}.inventory-item,.finding{break-inside:avoid}pre{max-height:none;overflow:visible}.facts{margin:12px 0}.notice{border:1px solid #ba8c22}.footer{font-size:11px}}
'''

APP=r'''
(()=>{'use strict';
const data=JSON.parse(document.getElementById('review-data').textContent), $=id=>document.getElementById(id);
const records=data.inventory.records, findings=data.inventory.findings;
let page=0,filtered=records;const searchIndex=new Map(records.map(r=>[r.id,JSON.stringify(r).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR')]));
function node(tag,text,cls){const value=document.createElement(tag);if(text!==undefined)value.textContent=String(text??'');if(cls)value.className=cls;return value;}
function appendText(parent,tag,text,cls){const value=node(tag,text,cls);parent.append(value);return value;}
function stringify(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
function field(parent,label,value,code=false){const dt=node('dt',label),dd=node('dd');dd.append(node(code?'code':'span',value??'Não informado'));parent.append(dt,dd);}
function values(parent,heading,items){appendText(parent,'h4',heading);if(!items?.length){appendText(parent,'p','Sem extração separada; veja a fundamentação.','muted');return;}const ul=node('ul');for(const item of items)ul.append(node('li',stringify(item)));parent.append(ul);}
function openID(id){if(!records.some(r=>r.id===id))return;$('search').value=id;$('kind').value='';$('file').value='';page=0;applyFilters();const entry=document.getElementById(id);if(entry){entry.open=true;entry.scrollIntoView({block:'start'});}try{history.replaceState(null,'','#'+id);}catch{if(location.hash!=='#'+id)location.hash=id;}}
function idButton(id){const b=node('button',id,'inline-id');b.type='button';b.addEventListener('click',()=>openID(id));return b;}
function recordElement(r){
 const detail=node('details',undefined,'inventory-item');detail.id=r.id;detail.dataset.inventoryId=r.id;
 const summary=node('summary'),title=node('span',undefined,'item-title');summary.append(node('span',r.id,'id'));title.append(node('span',r.current_file+' · '+r.current_function));title.append(node('small','Linhas '+r.current_range.start+'–'+r.current_range.end+' · '+r.disposition));summary.append(title);detail.append(summary);
 // Full conclusions are present for every entry in the embedded JSON. Details
 // are built only when opened so pagination remains responsive offline.
 let built=false;detail.addEventListener('toggle',()=>{if(!detail.open||built)return;built=true;const body=node('div',undefined,'item-body'),dl=node('dl');
 field(dl,'Situação','Revisado manualmente; testes não inferidos deste registro');field(dl,'Arquivo',r.current_file,true);field(dl,'Função',r.current_function,true);field(dl,'Trecho atual',r.current_range.start+'–'+r.current_range.end+'; ocorrência na linha '+r.current_line,true);field(dl,'Disposição',r.disposition,true);field(dl,'Proveniência',r.review_source+' · '+r.review_method,true);field(dl,'SHA-256 da fonte',r.source_sha256,true);body.append(dl);
 appendText(body,'h4','Fundamentação manual');appendText(body,'p',r.rationale);
 appendText(body,'h4','Guardas e condições do caminho');appendText(body,'p',r.guards_basis,'muted');if(r.guards.length)appendText(body,'pre',r.guards.join('\n'));
 appendText(body,'h4','Destino efetivo / efeito');appendText(body,'p',r.actual_sink);
 if(r.routes.length)values(body,'Rotas relacionadas',r.routes);if(r.function_calls.length)values(body,'Chamadas examinadas',r.function_calls);
 values(body,'Referências de testes — não são contagens de execução',r.relevant_tests.map(t=>typeof t==='string'?t:(t.path||stringify(t))+' · '+(t.reference_status||t.status||'Ponteiro de código')));
 appendText(body,'h4','Limite da evidência deste registro');appendText(body,'p',stringify(r.execution_claim),'muted');
 appendText(body,'h4','Ocorrência original');appendText(body,'p',r.original.file+':'+r.original.line+' · '+r.original.kind,'muted');appendText(body,'pre',r.original.excerpt+(r.original.excerpt_shortened?'\n[trecho abreviado; íntegra no JSON bruto]':''));
 if(r.findings_ids.length){appendText(body,'h4','Achados relacionados');for(const id of r.findings_ids)appendText(body,'code',id+' ');}
 detail.append(body);
 });return detail;
}
function render(){const size=Number($('pageSize').value)||records.length,totalPages=Math.max(1,Math.ceil(filtered.length/size));page=Math.max(0,Math.min(page,totalPages-1));const start=page*size,end=Math.min(filtered.length,start+size),root=$('inventory-list');root.replaceChildren();
 if(filtered.length)root.append(...filtered.slice(start,end).map(recordElement));else root.append(node('p','Nenhum registro corresponde a estes filtros. Limpe a busca ou tente outro termo.','empty'));
 $('results').textContent=filtered.length.toLocaleString('pt-BR')+' de '+records.length.toLocaleString('pt-BR')+' registros · '+(filtered.length?'exibindo '+(start+1)+'–'+end:'sem resultados');
 $('pageInfo').textContent='Página '+(page+1)+' de '+totalPages;$('previous').disabled=page===0;$('first').disabled=page===0;$('next').disabled=page===totalPages-1;$('last').disabled=page===totalPages-1;
 window.__ORION_REVIEW_STATE__={total:records.length,filtered:filtered.length,page:page+1,totalPages,rendered:end-start,findings:findings.length};
}
function applyFilters(){const term=$('search').value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR').trim(),kind=$('kind').value,file=$('file').value;filtered=records.filter(r=>(!term||searchIndex.get(r.id).includes(term))&&(!kind||r.original.kind===kind)&&(!file||r.current_file===file));page=0;render();}
for(const kind of [...new Set(records.map(r=>r.original.kind))].sort()){$('kind').append(new Option(kind,kind));}
for(const file of [...new Set(records.map(r=>r.current_file))].sort()){$('file').append(new Option(file,file));}
$('search').addEventListener('input',applyFilters);$('kind').addEventListener('change',applyFilters);$('file').addEventListener('change',applyFilters);$('pageSize').addEventListener('change',()=>{page=0;render();});
$('clear').addEventListener('click',()=>{$('search').value='';$('kind').value='';$('file').value='';applyFilters();$('search').focus();});
for(const [id,move] of [['first',()=>0],['previous',()=>page-1],['next',()=>page+1],['last',()=>Number.MAX_SAFE_INTEGER]])$(id).addEventListener('click',()=>{page=move();render();$('results').focus();});
function observationText(observation){const value=observation.record;const out=[];for(const [key,label] of [['resolution','Correção'],['fix','Correção'],['implementation_review_note','Releitura da implementação'],['proposal','Critério/proposta original'],['status_at_handoff','Situação no repasse'],['reproduction','Reprodução isolada'],['minimal_reproduction','Reprodução isolada'],['trigger','Gatilho']])if(value[key])out.push([label,value[key]]);return out;}
for(const finding of findings){const el=node('details',undefined,'finding'),summary=node('summary');appendText(summary,'span',finding.id+' · '+finding.title);appendText(summary,'span',finding.status==='fixed_tested_isolated'?'Corrigido · validação isolada registrada':'Correção relida em fonte · execução desse revisor não atestada','badge');el.append(summary);const body=node('div',undefined,'item-body');appendText(body,'p','Aceite limitado ao código e às evidências locais. A revisão manual não comprova entrega real nem publicação.','muted');
 for(const observation of finding.observations){const block=node('div',undefined,'finding-observation'),value=observation.record;appendText(block,'h4',observation.source);if(value.file||value.baseline_file)appendText(block,'code',(value.file||value.baseline_file)+(value.function?' · '+value.function:'')+(value.current_line?':'+value.current_line:''));for(const [label,text] of observationText(observation)){appendText(block,'h4',label);appendText(block,'p',text);}if(value.tests?.length)values(block,'Testes de regressão',value.tests);if(value.validation_run)appendText(block,'pre',stringify(value.validation_run));if(value.evidence)appendText(block,'pre',stringify(value.evidence));body.append(block);}
 if(finding.inventory_ids.length){appendText(body,'h4','Ocorrências do inventário');for(const id of finding.inventory_ids)body.append(idButton(id));}el.append(body);$('findings-list').append(el);}
function receiptPanel(id,item,fallback){const box=$(id);box.replaceChildren();if(!item){appendText(box,'h3',fallback);appendText(box,'p','Pendente · sem recibo de conclusão anexado.','pending');return;}const labels={pending:'Pendente',not_started:'Não iniciado',queued:'Na fila',in_progress:'Em andamento',passed:'Aprovado no recibo',succeeded:'Concluído no recibo',failed:'Falhou no recibo',cancelled:'Cancelado',blocked:'Bloqueado',rolled_back:'Revertido'};appendText(box,'h3',item.name||fallback);appendText(box,'p',labels[item.status]||item.status,'status');appendText(box,'p',item.summary||'Recibo sem descrição adicional.');if(item.commit)appendText(box,'code',item.commit);if(item.evidence?.length)appendText(box,'pre',stringify(item.evidence));appendText(box,'p','Informado pelo recibo fornecido pelo operador; o gerador deste relatório não consultou o provedor.','receipt-label');}
receiptPanel('ci-receipt',data.receipt?.data.ci,'CI do repositório');
const deployments=data.receipt?.data.deployments||[];if(deployments.length){$('deployments-receipt').replaceChildren();for(const item of deployments){const box=node('div',undefined,'subcard');box.id='deployment-'+$('deployments-receipt').children.length;$('deployments-receipt').append(box);receiptPanel(box.id,item,'Railway');}}else receiptPanel('deployments-receipt',null,'Deploy no Railway');
if(data.receipt){$('receipt-provenance').hidden=false;$('receipt-provenance').textContent='Recibo: '+data.receipt.path+' · SHA-256 '+data.receipt.sha256;$('publication-state').textContent=data.publication_status??'Status de publicação não informado no recibo.';$('publication-intro').textContent='Os estados abaixo foram informados no recibo fornecido pelo operador. Confira o escopo, o commit e as evidências de cada operação; estes dados não são inferidos da análise manual.';if(data.receipt.data.ci||data.receipt.data.deployments?.length)$('publication-test-state').textContent='Consultar recibos anexados';if(data.receipt.data.validation){$('validation-updates').hidden=false;appendText($('validation-updates'),'h3','Atualização dos testes fornecida no recibo');appendText($('validation-updates'),'p',data.receipt.data.validation.summary||'Resultados informados pelo executor.');appendText($('validation-updates'),'pre',stringify(data.receipt.data.validation));appendText($('validation-updates'),'p','Esta atualização é apresentada como fornecida; o gerador não executa testes nem soma grupos sobrepostos.','receipt-label');const broad=data.receipt.data.validation.results?.postgres_broad,status=typeof broad==='string'?broad:broad?.status,terminal=['passed','succeeded','completed','complete','failed','historical_run_failed','completed_with_failures'].includes(status),completeCounts=broad&&typeof broad==='object'&&Number.isSafeInteger(broad.passed)&&Number.isSafeInteger(broad.failed)&&!['pending','queued','in_progress','running','not_started'].includes(status);if(terminal||completeCounts||broad?.completed===true){$('pg-broad-base-state').textContent='Evidência-base anterior; ver recibo atual';$('pg-broad-base-state').classList.remove('pending');$('pg-broad-base-note').textContent='O resultado completo e sua reconciliação estão no recibo fornecido pelo executor. Esta linha preserva apenas a referência da evidência anterior.';}}}
render();if(/^#INV\d{4}$/.test(location.hash))openID(location.hash.slice(1));
window.addEventListener('hashchange',()=>{if(/^#INV\d{4}$/.test(location.hash))openID(location.hash.slice(1));});
})();
'''

def json_for_html(value):
    # The HTML parser must never see a literal closing script delimiter, HTML
    # entity opener, or Unicode JavaScript line separator from source material.
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')

def html_report(payload):
    inventory=payload['inventory'];summary=inventory['summary']
    findings_count=len(inventory['findings'])
    observations_count=sum(len(item['observations']) for item in inventory['findings'])
    source_count=len(inventory['source_files'])
    dependency_count=len(inventory.get('dependency_files',{}))
    total_sources=len(set(inventory['source_files'])|set(inventory.get('dependency_files',{})))
    html='''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>Orion · revisão manual do inventário</title><style>__STYLE__</style></head><body><main>
<header><p class="overline">Orion · revisão de comunicação · 20/10/2026</p><h1>Inventário revisado, ocorrência por ocorrência</h1><p class="lead">Os 1.247 candidatos da auditoria foram confrontados com as funções, os caminhos de chamada e os destinos efetivos no código. Este relatório reúne todas as conclusões e as correções encontradas.</p><p id="publication-state" class="status">__PUBLICATION_STATUS__</p></header>
<div class="facts"><div class="fact"><strong>1.247 /1.247</strong><span>ocorrências revisadas manualmente</span></div><div class="fact"><strong>__FINDINGS_COUNT__</strong><span>achados únicos · __OBSERVATIONS_COUNT__ observações e complemento QR documentado</span></div><div class="fact"><strong>__TOTAL_SOURCE_COUNT__</strong><span>arquivos de fonte e dependência validados por hash</span></div><div class="fact"><strong>0</strong><span>erros de ID, hash ou range no consolidado</span></div></div>
<div class="notice"><strong>Revisão manual completa não equivale a homologação em produção.</strong><p>Referências de teste são ponteiros de código. Apenas as execuções explicitamente listadas abaixo contam como testes desta tarefa. Resultados históricos, exemplos e textos prontos não comprovam envio, entrega ou integração externa.</p></div>
<nav aria-label="Índice do relatório"><a href="#scope">Escopo e preservação</a><a href="#tests">Testes atuais</a><a href="#findings">__FINDINGS_COUNT__ achados</a><a href="#inventory">Buscar 1.247 registros</a><a href="#publication">CI e publicação</a><a href="#acceptance">Aceite restante</a><a href="#artifacts">Arquivos e hashes</a></nav>
<section id="scope"><h2>O que esta revisão concluiu</h2><p>A classificação distingue erro HTTP, texto de interface, referência de editor, proposta, resposta factual e chamada de transporte. Cada ocorrência possui arquivo, função, linhas, fundamentação, guardas, destino e referências de teste próprias.</p><p>Os achados corrigem preservação de recibos após confirmação, tentativa Meta incerta, validade do consentimento de cancelamento e afirmações de estado sem prova atual. Na interface, corrigem estado de campanhas, fila de entrega, conteúdo personalizado, fuso e respostas atrasadas entre diálogos.</p><p class="source-note">Trabalho na branch <code>codex/orion-marcflow-integrations-20261020</code>. Os testes locais usam dados fictícios, HTTP substituído e infraestrutura isolada. A revisão não exige substituir credenciais, apagar sessões, reconfigurar a IA atual, ativar campanhas ou enviar mensagens reais. Qualquer execução operacional precisa de seu próprio recibo e escopo.</p><div class="grid"><div class="subcard"><h3>Análise manual</h3><p>1.247 IDs únicos, __SOURCE_COUNT__ fontes e __DEPENDENCY_COUNT__ dependências referenciadas (__TOTAL_SOURCE_COUNT__ arquivos distintos), 0 lacunas no índice. As fontes foram lidas por função e caminho; ferramentas apenas materializaram posições e verificaram a integridade.</p></div><div class="subcard"><h3>Limite da conclusão</h3><p>O snapshot de fontes permanece fixado pelos hashes. Alterar um arquivo depois da revisão exige reconciliar a evidência; o gerador não atualiza hashes para fazer a validação passar.</p></div></div></section>
<section id="tests"><h2>Execuções isoladas e sua proveniência</h2><p class="source-note">O commit <code>4b94c742c3b8030e0cdcbb6a1ed99b2e7e9396fb</code> consolida as correções iniciais. Os testes locais foram executados por grupo durante a preparação das alterações; não são atribuídos a uma árvore limpa desse commit. A CI do commit final verifica a árvore limpa, e seu estado está no recibo atual. O complemento QR foi consolidado no commit <code>832bc392c0b4e4fbd10e5028288d312a2cb0c88d</code> e está documentado no INV-JS-001. A tabela preserva as evidências anteriores; atualizações aparecem abaixo com seu próprio recibo.</p><div class="table"><table><thead><tr><th>Camada</th><th>Resultado-base</th><th>O que comprova e onde termina</th></tr></thead><tbody>
<tr><td>Backend unitário</td><td><strong class="ok">4.295 passaram</strong> · 2 ignorados<p><a href="backend-final.log">Log</a> · <a href="backend-final.xml">JUnit</a></p></td><td>Contratos e regressões locais, com rede externa bloqueada. Não comprova resposta de modelo real, ASR ou WhatsApp nativo.</td></tr>
<tr><td>Interface</td><td><strong class="ok">325 passaram</strong><p><a href="ui-all-after-manual.log">Log completo</a></p></td><td>DOM e HTTP fictício. Inclui 10 novas regressões do inventário; elas não são somadas outra vez.</td></tr>
<tr><td>QR bridge</td><td><strong class="ok">435 passaram</strong> · 4 ignorados no Windows<p><a href="qr-final.log">Log da rodada inicial</a></p></td><td>Adaptadores e transporte substituídos. O complemento QR documentado no INV-JS-001 tem7novos casos e442passaram no log <a href="qr-notice-final.log">qr-notice-final.log</a>; o resultado final também pode constar no recibo posterior. Os grupos se sobrepõem e não devem ser somados.</td></tr>
<tr><td>PostgreSQL / Redis direcionados</td><td><strong class="ok">Legado Meta: 13 passaram<br>Recebimento de produtos: 12 passaram</strong><p><a href="legacy-final.log">Legado</a> · <a href="fulfillment-final.log">Recebimento</a></p><p>Execuções anteriores: 24 + 12 casos, com sobreposição. Não somar como total distinto.</p></td><td>Contratos específicos e regressões com serviços locais. Confira os resultados amplos e a compatibilidade de adoção nos recibos; estes grupos isolados não comprovam homologação externa.</td></tr>
<tr><td>PostgreSQL amplo</td><td><strong id="pg-broad-base-state" class="pending">Pendente na evidência-base</strong></td><td id="pg-broad-base-note">As execuções direcionadas não substituem o resultado completo da matriz ampla e sua reconciliação.</td></tr>
<tr><td>CI / deploy</td><td><strong id="publication-test-state" class="pending">Pendentes</strong></td><td>Resultados operacionais aparecem na seção de recibos. Nenhum resultado local promove automaticamente esta linha para concluída.</td></tr>
</tbody></table></div><p class="muted">As contagens acima são as informadas pelo executor responsável ao gerar este relatório; não são calculadas somando referências ou execuções repetidas. Os recibos de CI/publicação são uma camada separada.</p><div id="validation-updates" class="subcard" hidden></div></section>
<section id="findings"><h2>__FINDINGS_COUNT__ achados e suas correções</h2><p>As __OBSERVATIONS_COUNT__ observações de revisão foram consolidadas em __FINDINGS_COUNT__ achados únicos. O complemento funcional do aviso QR está documentado no INV-JS-001, com7regressões próprias, sem contar um novo achado único. Abra cada item para ver gatilho, correção, releitura e a evidência registrada. Quando o revisor só releu a fonte, essa limitação é preservada.</p><div id="findings-list"></div></section>
<section id="inventory"><h2>Índice completo das 1.247 ocorrências</h2><p>A busca examina ID, fonte, função, fundamentação, guardas, destino e referências de teste. A paginação muda a apresentação; os 1.247 registros completos estão embutidos neste HTML, sem consulta à rede.</p><div class="filters" role="search" aria-label="Buscar no inventário"><label for="search">Buscar no inventário<input id="search" type="search" maxlength="300" autocomplete="off" placeholder="ID, arquivo, função, guarda ou teste"></label><label for="kind">Tipo original<select id="kind"><option value="">Todos os tipos</option></select></label><label for="file">Arquivo atual<select id="file"><option value="">Todos os arquivos</option></select></label></div><div class="pager"><button id="clear" type="button">Limpar filtros</button><label for="pageSize">Por página<select id="pageSize"><option value="25">25</option><option value="50">50</option><option value="100">100</option><option value="200">200</option><option value="1247">Todos · 1.247</option></select></label></div><p id="results" class="result-count" role="status" aria-live="polite" tabindex="-1"></p><div id="inventory-list"></div><div class="pager" aria-label="Paginação do inventário"><span id="pageInfo"></span><div><button id="first" type="button">Primeira</button><button id="previous" type="button">Anterior</button><button id="next" type="button">Próxima</button><button id="last" type="button">Última</button></div></div><noscript>Ative JavaScript para pesquisar/paginar o índice completo já contido neste arquivo, ou abra <a href="manual-inventory-compact.json">o JSON compacto com todos os registros</a>. Nenhuma rede é necessária.</noscript></section>
<section id="publication"><h2>CI e publicação: recibos separados</h2><p id="publication-intro">__PUBLICATION_INTRO__</p><div class="grid"><div id="ci-receipt" class="subcard"></div><div id="deployments-receipt" class="subcard"></div></div><p id="receipt-provenance" class="receipt-label" hidden></p><details><summary>Como atualizar esta seção</summary><p>Execute <code>build_review_report.py --receipt publication-receipt.json</code> com um recibo contendo estado explícito, commit completo quando houver e evidência concreta. O gerador apresenta esse recibo como fornecido pelo operador, sem consultar provedor nem inferir sucesso a partir dos testes locais.</p></details></section>
<section id="acceptance"><h2>Critérios de aceite e suas evidências</h2><div class="grid"><div class="subcard"><h3>Integração local ampla</h3><p>Confira os resultados de PostgreSQL/Redis amplo e da CI do commit final nos recibos atuais. As execuções direcionadas não substituem toda a matriz nem se somam como cobertura independente.</p></div><div class="subcard"><h3>Homologação externa</h3><p>Modelos, ASR, mídias e WhatsApp nativo exigem ensaio autorizado próprio. A revisão de guardas não comprova legibilidade física, entrega real, manutenção de sessão em provedor ou comportamento semântico completo dos anexos.</p></div><div class="subcard"><h3>Operação e preservação</h3><p>Antes de qualquer publicação real: confirmar o alvo e o commit, preservar dados/sessões, manter controles de execução e verificar os recibos do ambiente. Não apresentar um deploy anterior ou uma referência histórica como execução deste snapshot.</p></div><div class="subcard"><h3>Campanhas e mensagens</h3><p>Autorizar deploy não é evidência de campanha ativada ou mensagem enviada. Ativações, canais, consentimentos e testes reais têm critérios e registros próprios; preservar a IA corrente continua sendo requisito.</p></div></div></section>
<section id="artifacts"><h2>Artefatos completos e proveniência</h2><div class="raw-links"><a href="__COMPACT_LINK__">Índice compacto ·1.247 registros</a><a href="manual-inventory.json">Consolidado bruto preservado</a><a href="__SUMMARY_LINK__">Resumo do consolidado</a><a href="manual-inventory-validation.json">Validação dos registros</a><a href="review-report-manifest.json">Manifesto deste relatório</a></div><dl><dt>Inventário original</dt><dd><code>__ORIGINAL_SHA__</code></dd><dt>Consolidado bruto</dt><dd><code>__RAW_SHA__</code></dd><dt>Revisão consolidada</dt><dd><code>__CREATED_AT__</code></dd><dt>Gerado em UTC</dt><dd><code>__GENERATED_AT__</code></dd></dl><p class="muted">O JSON compacto mantém todas as conclusões e guardas. Trechos originais longos aparecem abreviados no índice; a íntegra permanece no bruto. Conteúdo de fonte é tratado como texto, inclusive marcação HTML e delimitadores de script.</p></section>
<p class="footer">Orion · evidência local rastreável. Este HTML funciona offline, não carrega bibliotecas, não abre conexões e não executa conteúdo do inventário.</p>
</main><script id="review-data" type="application/json">__DATA__</script><script id="review-app">__APP__</script></body></html>'''
    from html import escape
    replacements={'__STYLE__':STYLE,'__APP__':APP,'__DATA__':json_for_html(payload),
      '__FINDINGS_COUNT__':str(findings_count),'__OBSERVATIONS_COUNT__':str(observations_count),
      '__SOURCE_COUNT__':str(source_count),'__DEPENDENCY_COUNT__':str(dependency_count),'__TOTAL_SOURCE_COUNT__':str(total_sources),
      '__PUBLICATION_STATUS__':escape(payload['publication_status'] if payload['publication_status'] is not None else 'Status de publicação não informado no recibo.'),
      '__PUBLICATION_INTRO__':escape('Os estados abaixo foram informados no recibo fornecido pelo operador. Confira o escopo, o commit e as evidências de cada operação; estes dados não são inferidos da análise manual.' if payload['receipt'] else 'Publicação em preparação. A disponibilidade do Railway não comprova conclusão de deploy. O executor anexará os estados e recibos reais quando existirem.'),
      '__ORIGINAL_SHA__':escape(summary['original_inventory_sha256']),
      '__RAW_SHA__':escape(inventory['raw_source']['sha256']),
      '__CREATED_AT__':escape(summary['created_at_utc']),'__GENERATED_AT__':escape(payload['generated_at_utc']),
      '__COMPACT_LINK__':escape(payload.get('compact_link','manual-inventory-compact.json'),quote=True),
      '__SUMMARY_LINK__':escape(payload.get('summary_link','manual-inventory-summary.json'),quote=True)}
    # Replace the static template once: inserted untrusted data is never used
    # as another templating instruction.
    return re.sub('|'.join(re.escape(k) for k in replacements),lambda m:replacements[m.group()],html)

class CheckHTML(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.scripts=[];self.external=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag=='script':self.scripts.append(attrs)
        if (tag in {'script','img','iframe','link','object','embed'} and any(k in attrs for k in ['src','href','data'])):self.external.append((tag,attrs))

def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input',type=pathlib.Path,default=HERE/'manual-inventory.json')
    parser.add_argument('--output-dir',type=pathlib.Path,default=HERE)
    parser.add_argument('--receipt',type=pathlib.Path)
    parser.add_argument('--compact-link',default='manual-inventory-compact.json',help='Relative JSON filename after a documentation copy/rename.')
    parser.add_argument('--summary-link',default='manual-inventory-summary.json',help='Relative summary JSON filename after a documentation copy/rename.')
    args=parser.parse_args();raw=read(args.input);inventory=compact_inventory(raw,args.input);receipt=load_receipt(args.receipt)
    for value in [args.compact_link,args.summary_link]:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json',value):raise ValueError('Artifact link must be a plain relative JSON filename.')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    compact_path=args.output_dir/'manual-inventory-compact.json';compact_path.write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    publication_status=receipt['data'].get('publication_status') if receipt else 'Implementado/revisado; publicação em preparação'
    status_provenance={'scope':'operator_supplied_receipt' if receipt else 'historical_baseline_without_receipt',
        'receipt_field':'publication_status' if receipt else None,
        'missing_receipt_status_is_not_inferred':True}
    baseline_provenance={'scope':'historical_baseline','current_updates_field':'receipt.data.validation',
        'does_not_override_supplied_receipt':True,
        'description':'Earlier executor-supplied local test groups, retained as recorded; pending fields describe that baseline, not the current supplied CI/deployment/validation receipt.'}
    payload={'schema_version':1,'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'publication_status':publication_status,'status_source':publication_status,
        'publication_status_provenance':status_provenance,
        'compact_link':args.compact_link,'summary_link':args.summary_link,
        'inventory':inventory,'receipt':receipt,
        'test_evidence_provenance':baseline_provenance,
        'test_evidence_statement':{'evidence_layer':'historical_baseline','unit_passed':4295,'unit_skipped':2,'ui_passed':325,'qr_passed':435,'qr_skipped_windows':4,
            'qr_latest_notice_passed':442,'qr_notice_regressions_included':7,'qr_notice_runtime_commit':'832bc392c0b4e4fbd10e5028288d312a2cb0c88d',
            'pg_targeted_groups':[24,12],'legacy_meta_final_passed':13,'fulfillment_final_passed':12,
            'pg_groups_overlap':True,'pg_broad_status':'pending','adoption_compatibility_status':'pending',
            'basis':'Earlier counts supplied by the executor, preserved as a historical baseline; current receipt updates are separate; no sum of overlapping tests.'}}
    html=html_report(payload);check=CheckHTML();check.feed(html)
    assert len(check.ids)==len(set(check.ids)), 'Duplicate static DOM IDs'
    assert len(check.scripts)==2 and not check.external
    assert '__DATA__' not in html
    report=args.output_dir/'relatorio.html';report.write_text(html,encoding='utf-8')
    manifest={'schema_version':1,'generated_at_utc':payload['generated_at_utc'],
      'status_source':payload['publication_status'],'publication_status':payload['publication_status'],
      'publication_status_provenance':status_provenance,'raw_source':inventory['raw_source'],
      'reviewed_records':len(inventory['records']),'findings_unique':len(inventory['findings']),
      'source_snapshot_hashes_checked':len(set(inventory['source_files'])|set(inventory['dependency_files'])),
      'primary_source_files':len(inventory['source_files']),'dependency_files':len(inventory['dependency_files']),
      'findings_observations':sum(len(item['observations']) for item in inventory['findings']),
      'compact_sha256':sha(compact_path),'report_sha256':sha(report),
      'receipt':receipt,
      'offline_html_checks':{'scripts':2,'external_resource_tags':0,'static_id_duplicates':0},
      'execution_claims':payload['test_evidence_statement'],
      'execution_claims_provenance':baseline_provenance,
      'not_attested_scope':'Independent attestations not performed by this report generator; this does not negate any CI, deployment or validation evidence explicitly supplied in the receipt.',
      'not_attested':['By this generator: independent CI or Railway completion; supplied receipts are displayed verbatim',
          'By this generator: independent Broad PostgreSQL acceptance; supplied validation/CI evidence is retained',
          'By this generator: external model/ASR/native WhatsApp delivery']}
    (args.output_dir/'review-report-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(report),'records':len(inventory['records']),'findings':len(inventory['findings']),
      'compact_bytes':compact_path.stat().st_size,'html_bytes':report.stat().st_size,'receipt':bool(receipt)}))
if __name__=='__main__':main()
