export const presenceFlush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
export function presenceClock(start=0){
  let time=start,serial=0;const timers=new Map();
  return {now:()=>time,timers,
    schedule(fn,ms){const id={id:++serial,unref(){}};timers.set(id,{fn,at:time+ms,ms});return id;},
    cancel(id){timers.delete(id);},async advance(ms){
      await presenceFlush();const target=time+ms;
      for(;;){const next=[...timers].filter(([,task])=>task.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
        if(!next)break;timers.delete(next[0]);time=next[1].at;next[1].fn();await presenceFlush();}
      time=target;await presenceFlush();
    }};
}
