"""Generate a visibly synthetic hostile-source fixture; no repository writes."""
import copy,importlib.util,json,pathlib,re
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('review_builder',HERE/'build_review_report.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
html=(HERE/'relatorio.html').read_text(encoding='utf-8')
payload=json.loads(re.search(r'<script id="review-data" type="application/json">(.*?)</script>',html,re.S).group(1))
attack='probe_markup_8473 </script><img src=x onerror="window.__REPORT_INJECTED__=true"><script>window.__REPORT_INJECTED__=true</script> & __APP__ \u2028\u2029'
for row in [payload['inventory']['records'][0],payload['inventory']['records'][-1]]:
 row['rationale']=attack;row['actual_sink']=attack;row['guards']=[attack];row['relevant_tests']=[{'path':attack,'reference_status':attack}]
payload['inventory']['findings'][0]['title']='probe_finding_markup '+attack
payload['inventory']['findings'][0]['observations'][0]['record']['resolution']=attack
out=HERE/'review-security-fixture.html';out.write_text(module.html_report(payload),encoding='utf-8')
checker=module.CheckHTML();checker.feed(out.read_text(encoding='utf-8'))
assert len(checker.scripts)==2 and not checker.external
assert module.json_for_html({'probe':attack}).count('</script>')==0
print('Hostile source fixture escaped; HTMLParser found only2script tags and0external resource tags.')
