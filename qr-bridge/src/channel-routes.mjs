/** Optional routes installed alongside, never replacing legacy pilot routes. */
import {createChannelBroker} from './channel-broker.mjs';
import {createChannelManager} from './channel-manager.mjs';
import {createBaileysChannelSession} from './channel-session.mjs';
import {createChannelResumeStore} from './channel-resume.mjs';
import {validGroupContactsRequest} from './device-group-contacts.mjs';
import {validNumberCheckRequest} from './device-number-check.mjs';
import {validGroupAddRequest} from './device-group-add.mjs';

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
    const numberCheckMatch=uri.pathname.match(/^\/internal\/channels\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/device\/number-check\/check$/);
    if(numberCheckMatch&&!uri.search){
      const id=numberCheckMatch[1],key=req.headers['x-orion-channel-key'];
      if(typeof key!=='string'||key.length<32||key.length>256){response(res,401,{error:'channel_unauthorized'});return true;}
      if(req.method!=='POST'){response(res,405,{error:'method_not_allowed'});return true;}
      try{
        const input=await body(req);
        if(!validNumberCheckRequest(input))throw Error('number_check_unavailable');
        response(res,200,await getManager().deviceNumberCheck(id,key,input));
      }catch{response(res,409,{error:'number_check_unavailable'});}
      return true;
    }
    const groupAddMatch=uri.pathname.match(/^\/internal\/channels\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/device\/group-add\/(groups|members|add|lookup)$/);
    if(groupAddMatch&&!uri.search){
      const [,id,action]=groupAddMatch,key=req.headers['x-orion-channel-key'];
      if(typeof key!=='string'||key.length<32||key.length>256){response(res,401,{error:'channel_unauthorized'});return true;}
      if(req.method!=='POST'){response(res,405,{error:'method_not_allowed'});return true;}
      try{
        const input=await body(req);
        if(!validGroupAddRequest(action,input))throw Error('group_add_unavailable');
        response(res,200,await getManager().deviceGroupAdd(id,key,action,input));
      }catch{response(res,409,{error:'group_add_unavailable'});}
      return true;
    }
    const groupContactsMatch=uri.pathname.match(/^\/internal\/channels\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/device\/group-contacts\/(groups|members)$/);
    if(groupContactsMatch&&!uri.search){
      const [,id,action]=groupContactsMatch,key=req.headers['x-orion-channel-key'];
      if(typeof key!=='string'||key.length<32||key.length>256){response(res,401,{error:'channel_unauthorized'});return true;}
      if(req.method!=='POST'){response(res,405,{error:'method_not_allowed'});return true;}
      try{
        const input=await body(req);
        if(!validGroupContactsRequest(action,input))throw Error('group_contacts_unavailable');
        response(res,200,await getManager().deviceGroupContacts(id,key,action,input));
      }catch{response(res,409,{error:'group_contacts_unavailable'});}
      return true;
    }
    const diagnosticMatch=uri.pathname.match(/^\/internal\/channels\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/device\/diagnostics\/(proxy|operations\/([0-9a-f-]{36}))$/);
    if(diagnosticMatch&&!uri.search){
      const [,id,action,requestId]=diagnosticMatch,key=req.headers['x-orion-channel-key'];
      if(typeof key!=='string'||key.length<32||key.length>256){response(res,401,{error:'channel_unauthorized'});return true;}
      if(req.method!==(requestId?'GET':'POST')){response(res,405,{error:'method_not_allowed'});return true;}
      try{
        const api=getManager();let result;
        if(requestId)result=await api.deviceDiagnosticOperation(id,key,requestId);
        else{
          const input=await body(req),fields=['request_id','device_id','generation','settings_revision'];
          if(Object.keys(input).some(field=>!fields.includes(field))||fields.some(field=>!(field in input)))throw Error('invalid_device_diagnostic');
          result=await api.deviceProxyTest(id,key,input);
        }
        response(res,requestId?200:202,result);
      }catch{response(res,409,{error:'device_diagnostic_unavailable'});}
      return true;
    }
    const deviceMatch=uri.pathname.match(/^\/internal\/channels\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/device\/(status|qr|diagnostics|connect|pair|restart|relink|remove|operations\/([0-9a-f-]{36}))$/);
    if(deviceMatch&&!uri.search){
      const [,id,action,operationId]=deviceMatch,key=req.headers['x-orion-channel-key'];
      if(typeof key!=='string'||key.length<32||key.length>256){response(res,401,{error:'channel_unauthorized'});return true;}
      const read=['status','qr','diagnostics'].includes(action)||!!operationId;
      if(req.method!==(read?'GET':'POST')){response(res,405,{error:'method_not_allowed'});return true;}
      try{
        const api=getManager();let result;
        if(action==='status')result=await api.deviceStatus(id,key);
        else if(action==='diagnostics')result=await api.deviceDiagnostics(id,key);
        else if(operationId)result=await api.deviceOperation(id,key,operationId);
        else if(action==='qr'){
          const scope=await api.deviceStatus(id,key),qr=await api.qr(id,key);
          result={device_id:scope.device_id,generation:scope.generation,settings_revision:scope.settings_revision,image:null,expires_in_ms:0};
          if(qr?.value&&qr.expires_in_ms>0){
            const image=await renderQr(qr.value),fresh=await api.qr(id,key),latest=await api.deviceStatus(id,key);
            if(fresh?.value===qr.value&&fresh.expires_in_ms>0&&latest.device_id===scope.device_id&&latest.generation===scope.generation&&latest.settings_revision===scope.settings_revision)
              result={...result,image,expires_in_ms:Math.floor(fresh.expires_in_ms)};
          }
        }else{
          const input=await body(req),fields=['device_id','generation','settings_revision','operation_id',...(action==='pair'?['phone']:[])];
          if(Object.keys(input).some(k=>!fields.includes(k))||fields.some(k=>!(k in input)))throw Error('invalid_device_request');
          result=await api.deviceControl(id,key,action,input);
        }
        response(res,200,result);
      }catch{response(res,409,{error:'device_operation_unavailable'});}
      return true;
    }
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
          ('source' in input&&!(input.source==='appointment'&&input.control_revision===undefined||input.source==='manual'&&Number.isSafeInteger(input.control_revision)&&input.control_revision>=1))||
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
  route.close=()=>manager?.close();
  return route;
}
