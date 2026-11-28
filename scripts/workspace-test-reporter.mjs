// Stable, machine-comparable test outcomes; ignore durations and stack line shifts.
export default async function* reporter(source){
  for await(const event of source){
    const d=event.data;
    if(event.type==='test:fail'){
      const err=d.details?.error,root=err?.cause??err;
      const file=(d.file??'').split('/orion-assistente/').at(-1);
      yield 'ORIONQA '+JSON.stringify({kind:'fail',file,name:d.name,error:{
        name:root?.name??'',message:root?.message??'',code:root?.code??'',
        actual:root?.actual,expected:root?.expected,operator:root?.operator
      }})+'\n';
    }
    if(event.type==='test:summary'&&!d.file){
      const c=d.counts;
      yield 'ORIONQA '+JSON.stringify({kind:'summary',tests:c.tests,passed:c.passed,failed:c.failed,skipped:c.skipped,cancelled:c.cancelled})+'\n';
    }
  }
}
