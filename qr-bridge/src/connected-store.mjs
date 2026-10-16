/** Connected-device control state. No fabricated WhatsApp history. */
import fs from "node:fs";
import path from "node:path";
import {createCipheriv,createDecipheriv,createHash,createHmac,randomBytes} from "node:crypto";

const defaults=()=>({
  chatbot:false,
  transcription:false,
  language:"auto",
  callBlocking:false,
  callReply:false,
  callReplyText:"Não podemos atender ligações agora. Por favor, envie uma mensagem.",
  allowedCallers:[],
  callSchedule:false,
  callHours:{start:"09:00",end:"18:00",weekdays:[1,2,3,4,5]}
});
const keyOf=secret=>createHash("sha256").update("orion-connected-v1:"+secret).digest();
function decrypt(file,key){
  const buf=fs.readFileSync(file);
  if(buf.length<29)throw Error("Arquivo de configurações inválido");
  const decipher=createDecipheriv("aes-256-gcm",key,buf.subarray(0,12));
  decipher.setAuthTag(buf.subarray(12,28));
  return JSON.parse(Buffer.concat([decipher.update(buf.subarray(28)),decipher.final()]).toString("utf8"));
}
function atomicWrite(file,data,key){
  const nonce=randomBytes(12),cipher=createCipheriv("aes-256-gcm",key,nonce);
  const ciphertext=Buffer.concat([cipher.update(JSON.stringify(data),"utf8"),cipher.final()]);
  const temporary=file+".tmp-"+process.pid;
  try{
    fs.writeFileSync(temporary,Buffer.concat([nonce,cipher.getAuthTag(),ciphertext]),{mode:0o600,flag:"wx"});
    fs.renameSync(temporary,file);
  }finally{try{fs.unlinkSync(temporary)}catch{}}
}
export function validateSettings(input,capabilities={}){
  if(!input||typeof input!=="object"||Array.isArray(input))throw Error("Configuração inválida");
  const keys=Object.keys(defaults());
  if(Object.keys(input).some(x=>!keys.includes(x)))throw Error("Campo não autorizado");
  const out={...defaults(),...input};
  for(const k of ["chatbot","transcription","callBlocking","callReply","callSchedule"])
    if(typeof out[k]!=="boolean")throw Error("Opção inválida: "+k);
  if(out.chatbot && !capabilities.chatbot)throw Error("Chatbot não integrado ao canal vinculado");
  if(out.transcription && !capabilities.transcription)throw Error("Serviço de transcrição não configurado");
  if(out.callBlocking && capabilities.callBlocking===false)
    throw Error("Bloqueio de chamadas não suportado nesta conexão");
  if(!["auto","pt","en","es"].includes(out.language))throw Error("Idioma inválido");
  if(typeof out.callReplyText!=="string" || out.callReplyText.length>240)
    throw Error("Mensagem de chamada inválida");
  if(!Array.isArray(out.allowedCallers)||out.allowedCallers.length>30||
      out.allowedCallers.some(x=>typeof x!=="string"||!/^[1-9]\d{7,14}$/.test(x))||
      new Set(out.allowedCallers).size!==out.allowedCallers.length)
    throw Error("Lista de números permitidos inválida");
  const h=out.callHours;
  if(!h||typeof h!=="object"||Array.isArray(h)||
     !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(h.start)||
     !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(h.end)||
     h.start>=h.end||!Array.isArray(h.weekdays)||h.weekdays.length>7||
     h.weekdays.some(x=>!Number.isInteger(x)||x<0||x>6)||
     new Set(h.weekdays).size!==h.weekdays.length)
    throw Error("Janela de chamadas inválida");
  if(out.callReply && !out.callBlocking)throw Error("A resposta exige bloqueio de chamadas");
  return out;
}
export function shouldRejectCall(settings,phone,when=new Date(),timezone="America/Campo_Grande"){
  if(!settings.callBlocking || settings.allowedCallers.includes(phone))return false;
  if(!settings.callSchedule)return true;
  const parts=Object.fromEntries(new Intl.DateTimeFormat("en-US",{
    timeZone:timezone,hourCycle:"h23",weekday:"short",hour:"2-digit",minute:"2-digit"
  }).formatToParts(when).filter(p=>p.type!=="literal").map(p=>[p.type,p.value]));
  const day=["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].indexOf(parts.weekday);
  const clock=parts.hour.padStart(2,"0")+":"+parts.minute;
  return !(settings.callHours.weekdays.includes(day) &&
    clock>=settings.callHours.start && clock<settings.callHours.end);
}
export function makeConnectedStore({directory,secret,timezone="America/Campo_Grande"}){
  if(!secret||secret.length<32)throw Error("Chave de armazenamento indisponível");
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const key=keyOf(secret),file=path.join(directory,"connected-control.enc");
  let state={identity:null,settings:defaults(),total:0,byDay:{},seen:[],trackingSince:null};
  let healthy=true;
  if(fs.existsSync(file)){
    try{const data=decrypt(file,key);if(data&&typeof data==="object"&&Number.isSafeInteger(data.total)&&data.total>=0)state=data;else healthy=false}
    catch{healthy=false}
  }
  const save=()=>{if(!healthy)throw Error("Armazenamento conectado requer reparo");
    atomicWrite(file,state,key)};
  const dayFormat=new Intl.DateTimeFormat("en-CA",{
    timeZone:timezone,year:"numeric",month:"2-digit",day:"2-digit"
  });
  const dayFor=date=>{
    const fields=Object.fromEntries(dayFormat.formatToParts(date)
      .filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));
    return fields.year+"-"+fields.month+"-"+fields.day;
  };
  return {
    health:()=>healthy?"ready":"unavailable",
    setIdentity(jid){
      if(!jid||typeof jid!=="string")return;
      const hash=createHmac("sha256",key).update(jid.split(":")[0].split("@")[0]).digest("hex");
      if(state.identity!==hash){
        if(!healthy)throw Error("Armazenamento conectado requer reparo");
        state={identity:hash,settings:defaults(),total:0,byDay:{},seen:[],
          trackingSince:new Date().toISOString()};
        save();
      }
    },
    settings:()=>structuredClone(state.settings),
    update(payload,capabilities){
      if(!state.identity||!healthy)throw Error("Dispositivo ainda indisponível");
      const next=validateSettings(payload,capabilities);
      state.settings=next;save();return structuredClone(next);
    },
    record(id,at){
      if(!healthy||!state.identity||!id||!Number.isFinite(at))return false;
      const token=createHmac("sha256",key).update(String(id)).digest("hex");
      if(state.seen.includes(token))return false;
      state.seen.push(token);if(state.seen.length>3000)state.seen.shift();
      const day=dayFor(new Date(at));
      state.byDay[day]=(state.byDay[day]||0)+1;state.total++;
      // Statistics are bounded; never claim to include prior history.
      const days=Object.keys(state.byDay).sort();
      for(const old of days.slice(0,Math.max(0,days.length-45)))delete state.byDay[old];
      save();return true;
    },
    stats(now=new Date()){
      if(!healthy||!state.identity)return {available:false,scope:"not_ready"};
      const counts=n=>Array.from({length:n},(_,i)=>{
        const date=new Date(now.getTime());
        date.setUTCDate(date.getUTCDate()-i);
        return state.byDay[dayFor(date)]||0;
      }).reduce((a,b)=>a+b,0);
      return {available:true,scope:"since_tracking_enabled",timezone,trackingSince:state.trackingSince,
        today:counts(1),last7Days:counts(7),last30Days:counts(30),total:state.total};
    }
  };
}
