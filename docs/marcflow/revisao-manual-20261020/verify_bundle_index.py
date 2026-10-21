"""Verify raw evidence bytes are exactly what Git will commit."""
from pathlib import Path
import hashlib,json,subprocess
root=Path(r'C:\Users\kayky\Documents\Playground\orion-marcflow-20261020')
prefix='docs/marcflow/revisao-manual-20261020/'
result=subprocess.run(['git','ls-files','--stage','-z','--',prefix],cwd=root,check=True,capture_output=True)
checked=0;total=0;mismatches=[]
for entry in result.stdout.split(b'\0'):
    if not entry:continue
    header,name=entry.split(b'\t',1)
    mode,oid,stage=header.decode().split()
    assert stage=='0' and mode=='100644'
    path=name.decode();data=(root/path).read_bytes()
    actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if actual!=oid:mismatches.append(path)
    checked+=1;total+=len(data)
assert checked>100
record={'status':'passed' if not mismatches else 'failed','raw_artifacts_checked':checked,'total_bytes':total,'index_byte_mismatches':mismatches,'scope':'raw working-file bytes versus staged Git blob IDs; source/runtime not executed'}
Path(__file__).with_name('bundle-index-byte-validation.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
raise SystemExit(bool(mismatches))

