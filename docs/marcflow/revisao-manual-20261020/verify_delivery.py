from pathlib import Path
from html.parser import HTMLParser
import hashlib,json,sys

folder=Path(sys.argv[1]).resolve()
repo=folder.parents[2]
def read(name):return json.loads((folder/name).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
class Links(HTMLParser):
    links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            for key,value in attrs:
                if key=='href' and value and not value.startswith(('#','https:','http:')):self.links.append(value)
parser=Links();parser.feed((folder/'relatorio.html').read_text(encoding='utf-8'))
missing=[name for name in parser.links if not (folder/name.split('#')[0]).is_file()]
assert not missing,missing
manifest=read('review-report-manifest.json');qa=read('review-report-validation.json')
for name,key in [('relatorio.html','report_sha256'),('manual-inventory-compact.json','compact_sha256')]:
    assert sha(folder/name)==manifest[key]==qa[key],name
compact=read('manual-inventory-compact.json')
assert len(compact['records'])==1247 and compact['summary']['all_records_reviewed']
assert not compact['summary']['stale_sources'] and not compact['summary']['errors']
assert len({r['id'] for r in compact['records']})==1247
source_union={**compact.get('dependency_files',{}),**compact['source_files']}
for name,digest in source_union.items():assert sha(repo/name)==digest,name
receipt=read('publication-receipt.json')
assert sha(folder/'publication-receipt.json')==manifest['receipt']['sha256']
result={'records':1247,'relative_links_checked':len(parser.links),'missing_links':missing,
    'current_record_sources_checked':len(compact['source_files']),
    'current_source_and_dependency_files_checked':len(source_union),
    'report_and_receipt_hashes_match':True,
    'html_security_qa_passed':qa['injected_markup_nodes']==0 and qa['network_attempts']==0 and not qa['jsdom_errors'],
    'publication_status':receipt['publication_status'],'application_tests_rerun':False}
assert result['html_security_qa_passed']
(folder/'delivery-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
