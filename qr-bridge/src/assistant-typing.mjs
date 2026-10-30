/** Targeted typing only. No messages, presence subscription or privacy changes. */
export function makeAssistantTyping({socket,pilotPhone,isConnected,isAllowed,
  now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,
  pollMs=100,refreshMs=4000,maxDurationMs=20000,maxPauseMs=800,transactional=false}={}){
  if(typeof pilotPhone!=='function'||typeof isConnected!=='function'||typeof isAllowed!=='function'||
      !Number.isSafeInteger(pollMs)||pollMs<1||pollMs>1000||
      !Number.isSafeInteger(refreshMs)||refreshMs<pollMs||
      !Number.isSafeInteger(maxDurationMs)||maxDurationMs<1||maxDurationMs>20000||
      typeof transactional!=='boolean'||
      !Number.isSafeInteger(maxPauseMs)||maxPauseMs<0||maxPauseMs>(transactional?2000:800))
    throw Error('Invalid typing presence configuration');
  let active=null;
  const quiet=Object.freeze({beforeReply:async()=>{},stop(){}});
  const safely=fn=>{try{return fn()===true;}catch{return false;}};
  function begin({phone,chatJid,isCurrent=()=>false}={}){
    const privateJid=typeof chatJid==='string'&&chatJid.match(/^(\d{8,30})(?::\d{1,5})?@(s\.whatsapp\.net|lid)$/);
    // Transactional confirmation/notifications have their own explicit,
    // verified recipient check. The AI pilot still requires its original PN.
    const valid=transactional?/^(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$/:/^55[1-9]\d9\d{8}$/;
    const allowed=()=>safely(()=>typeof phone==='string'&&valid.test(phone)&&
      phone===pilotPhone()&&isConnected()===true&&isAllowed(phone)===true);
    const current=()=>allowed()&&safely(isCurrent);
    if(!privateJid||(privateJid[2]==='s.whatsapp.net'&&privateJid[1]!==phone)||
        typeof socket?.sendPresenceUpdate!=='function'||!current())return quiet;
    active?.stop();
    const started=now(),jid=phone+'@s.whatsapp.net';
    let stopped=false,timer,pauseTimer,pauseResolve,inFlight=false,lastComposing=started;
    const lease={beforeReply,stop};active=lease;
    function fire(type){
      try{
        const result=socket.sendPresenceUpdate(type,jid);
        if(type==='composing')inFlight=true;
        Promise.resolve(result).catch(()=>{if(type==='composing')stop();})
          .finally(()=>{if(type==='composing')inFlight=false;});
        return true;
      }catch{return false;}
    }
    function stop(){
      if(stopped)return;
      stopped=true;cancel(timer);cancel(pauseTimer);pauseResolve?.();pauseResolve=null;
      if(active===lease){
        active=null;
        // Revision changes stop this lease, but cannot pause a newer lease.
        // A replaced/disconnected socket or revoked recipient gets no traffic.
        if(allowed())fire('paused');
      }
    }
    function monitor(){
      if(stopped)return;
      if(active!==lease||!current()||now()-started>=maxDurationMs){stop();return;}
      if(!inFlight&&now()-lastComposing>=refreshMs){
        lastComposing=now();if(!fire('composing')){stop();return;}
      }
      timer=schedule(monitor,pollMs);timer?.unref?.();
    }
    async function beforeReply(text){
      if(stopped||active!==lease||!current()||typeof text!=='string'||!text.trim())return;
      // Work already spent on inference counts toward this brief visual pause.
      // A long operation is sent immediately when it is ready.
      const target=transactional?
        Math.min(maxPauseMs,Math.max(900,[...text].length*13)):
        Math.min(maxPauseMs,Math.max(150,[...text].length*8));
      const remaining=Math.min(maxPauseMs,Math.max(0,target-(now()-started)));
      if(!remaining)return;
      await new Promise(resolve=>{
        pauseResolve=resolve;
        pauseTimer=schedule(()=>{pauseResolve=null;resolve();},remaining);
      });
    }
    if(!fire('composing'))stop();
    if(!stopped){timer=schedule(monitor,pollMs);timer?.unref?.();}
    return lease;
  }
  return Object.freeze({begin,async run(options,work){
    const lease=begin(options);
    try{return await work(lease);}finally{lease.stop();}
  }});
}
