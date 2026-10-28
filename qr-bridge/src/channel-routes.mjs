/** Optional routes installed alongside, never replacing legacy pilot routes. */
import {createChannelBroker} from './channel-broker.mjs';
import {createChannelManager} from './channel-manager.mjs';
import {createBaileysChannelSession} from './channel-session.mjs';
import {createChannelResumeStore} from './channel-resume.mjs';

function response(res,status,data){
  res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',
    'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'});res.end(JSON.stringify(data));
}
async function body(req){
  let size=0;const chunks=[];
  for await(const chunk of req){size+=chunk.length;if(size>8192)throw Error('invalid_channel_request');chunks.push(chunk);}
  const value=JSON.parse(Buffer.concat(chunks).toString());
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('invalid_channel_request');
  return value;
}
export function createChannelRoutes({env=process.env,managerFactory=createChannelManager,brokerFactory=createChannelBroker,sessionFactory=createBaileysChannelSession,
  renderQr=async value=>(await import('qrcode')).default.toDataURL(value,{errorCorrectionLevel:'M',margin:2,width:370})}={}){
  // Construction/GETs perform no network or socket mutation. Resume is an
  // explicit server lifecycle hook, backed by an encrypted connection intent.
  let manager;
  const getManager=()=>manager||(manager=managerFactory({broker:brokerFactory({apiUrl:env.QR_CHANNEL_BROKER_URL}),
    createSession:sessionFactory,root:'/data/orion-channels',
    resumeStore:env.QR_CHANNELS_ENABLED==='1'&&env.QR_BRIDGE_TOKEN?.length>=32?
      createChannelResumeStore({root:'/data/orion-channels',secret:env.QR_BRIDGE_TOKEN}):null}));
  const route=async function route(req,res){
    const uri=new URL(req.url||'/','http://internal');
    if(!uri.pathname.startsWith('/internal/channels/'))return false;
    if(env.QR_CHANNELS_ENABLED!=='1'){response(res,423,{error:'channels_disabled'});return true;}
    const match=uri.pathname.match(/^\/internal\/channels\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/(status|start|stop|qr|pair|send|operations\/([0-9a-f-]{36}))$/);
    if(!match||uri.search){response(res,404,{error:'not_found'});return true;}
    const [,id,action,operationId]=match,key=req.headers['x-orion-channel-key'];
    if(typeof key!=='string'||key.length<32||key.length>256){response(res,401,{error:'channel_unauthorized'});return true;}
    if(!['GET','POST'].includes(req.method)||(req.method==='GET')!==(['status','qr'].includes(action)||Boolean(operationId))){
      response(res,405,{error:'method_not_allowed'});return true;}
    try{
      const api=getManager();let result;
      if(action==='status')result=await api.status(id,key);
      else if(operationId)result=await api.operation(id,key,operationId);
      else if(action==='qr'){
        const qr=await api.qr(id,key);
        result={image:null,expires_in_ms:0};
        if(qr?.value&&qr.expires_in_ms>0){
          const image=await renderQr(qr.value),fresh=await api.qr(id,key);
          if(fresh?.value===qr.value&&fresh.expires_in_ms>0)result={image,expires_in_ms:Math.floor(fresh.expires_in_ms)};
        }
      }
      else {
        const input=await body(req),required=action==='send'?['to','text','operation_id']:action==='pair'?['phone']:[],allowed=[...required,...(action==='send'?['source','control_revision']:[])];
        if(Object.keys(input).some(k=>!allowed.includes(k))||required.some(k=>!(k in input))||
          ('source' in input&&(input.source!=='manual'||!Number.isSafeInteger(input.control_revision)||input.control_revision<1))||
          ('control_revision' in input&&input.source!=='manual')||
          (action==='pair'&&(typeof input.phone!=='string'||input.phone!==input.phone.trim()||!/^[1-9]\d{7,14}$/.test(input.phone))))throw Error('invalid_channel_request');
        if(action==='start')result=await api.start(id,key);
        else if(action==='stop'){await api.stop(id,key);result={status:'stopped'};}
        else if(action==='pair')result=await api.pair(id,key,input);
        else result=await api.send(id,key,input);
      }
      response(res,200,result);
    }catch{
      // Do not echo provider errors, IDs, credentials, phone numbers or text.
      response(res,409,{error:'channel_operation_unavailable'});
    }
    return true;
  };
  route.resume=async()=>env.QR_CHANNELS_ENABLED==='1'?getManager().resume():{resumed:0};
  route.adopt=(id,key)=>getManager().start(id,key);
  route.send=(id,key,payload)=>getManager().send(id,key,payload);
  return route;
}
