import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import {
  validPhone,normalizeFromJid,extractText,resolveMessagePhone,
  freshQR,formatPairingCode,connectionPhase,reconnectPlan
} from "../src/core.mjs";

test("telefone Brasil com DDI e DDD é normalizado",()=>{
  assert.equal(validPhone("+55 (11) 99999-9999"),"5511999999999");
  assert.equal(validPhone("5511999999999"),"5511999999999");
});

test("número internacional é aceito sem inventar DDI",()=>{
  assert.equal(validPhone("+595 992 123456"),"595992123456");
});

test("telefones vazios, curtos e perigosos são rejeitados",()=>{
  for(const number of ["","551","abc","+55 11 99<script>","000000000","5521997557364;rm -rf /"]){
    assert.throws(()=>validPhone(number));
  }
});

test("nunca confundir grupo e LID com número de telefone",()=>{
  assert.equal(normalizeFromJid("5511999999999@s.whatsapp.net"),"5511999999999");
  assert.equal(normalizeFromJid("5511999999999:2@s.whatsapp.net"),"5511999999999");
  assert.equal(normalizeFromJid("1234567890@lid"),null);
  assert.equal(normalizeFromJid("12345@g.us"),null);
});

test("remetente PN é usado sem acessar mapeamentos",async()=>{
  assert.equal(await resolveMessagePhone({remoteJid:"5511999999999@s.whatsapp.net"},null),
    "5511999999999");
});

test("fallback remoteJidAlt aceita apenas PN, não LID",async()=>{
  assert.equal(await resolveMessagePhone({remoteJid:"1234567890@lid",
       remoteJidAlt:"5511999999999@s.whatsapp.net"},null),"5511999999999");
});

test("LID deve ser mapeado para PN no repositório autenticado",async()=>{
  const fake={signalRepository:{lidMapping:{
    async getPNForLID(lid){
      assert.equal(lid,"1234567890@lid");
      return "5511999999999@s.whatsapp.net";
    }
  }}};
  assert.equal(await resolveMessagePhone({remoteJid:"1234567890@lid"},fake),
    "5511999999999");
});

test("LID sem mapeamento é descartado, nunca vira telefone",async()=>{
  assert.equal(await resolveMessagePhone({remoteJid:"1234567890@lid"},{}),null);
  const bad={signalRepository:{lidMapping:{
    async getPNForLID(){throw Error("db")}
  }}};
  assert.equal(await resolveMessagePhone({remoteJid:"1234567890@lid"},bad),null);
});

test("mensagem texto e texto efêmero/caption",()=>{
  assert.equal(extractText({conversation:"Olá"}),"Olá");
  assert.equal(extractText({extendedTextMessage:{text:"Tudo bem?"}}),"Tudo bem?");
  assert.equal(extractText({ephemeralMessage:{message:{conversation:"Novo"}}}),"Novo");
  assert.equal(extractText({imageMessage:{caption:"Legenda"}}),"Legenda");
  assert.equal(extractText({videoMessage:{caption:"Vídeo"}}),"Vídeo");
  assert.equal(extractText({documentMessage:{mimetype:"application/pdf"}}),null);
});

test("QR expirado, futuro ou zero é recusado",()=>{
  assert.equal(freshQR(1000,2000),true);
  assert.equal(freshQR(1000,46000),false);
  assert.equal(freshQR(1000,500),false);
  assert.equal(freshQR(0,1000),false);
});

test("formatação de código de emparelhamento de oito caracteres",()=>{
  assert.equal(formatPairingCode("ab12cd34"),"AB12-CD34");
  assert.equal(formatPairingCode("AB12-CD34"),"AB12-CD34");
  for(const invalid of ["short","123456789","HH12<script>",""]){
    assert.throws(()=>formatPairingCode(invalid));
  }
});

test("socket HTTP IPv6 privado aceita conexões em ::1",async()=>{
  const server=http.createServer((req,res)=>{
    res.writeHead(200,{"Content-Type":"application/json"});
    res.end('{"status":"ok"}');
  });
  await new Promise((resolve,reject)=>{
    server.once("error",reject);
    server.listen({port:0,host:"::",ipv6Only:false},resolve);
  });
  try{
    const port=server.address().port;
    const body=await new Promise((resolve,reject)=>{
      http.get({hostname:"::1",family:6,port,path:"/health",timeout:3000},res=>{
        let out="";res.on("data",part=>out+=part);
        res.on("end",()=>resolve(out));
      }).on("error",reject);
    });
    assert.deepEqual(JSON.parse(body),{status:"ok"});
  }finally{
    await new Promise(resolve=>server.close(resolve));
  }
});

test("QR em preparação sinaliza atraso após 25 segundos",()=>{
  assert.equal(connectionPhase("connecting",0,1000,25000),"connecting");
  assert.equal(connectionPhase("connecting",0,1000,26001),"qr_delayed");
  assert.equal(connectionPhase("reconnecting",0,1000,26001),"reconnecting");
});

test("QR vence após 45 segundos, mas não deixa estado conectado",()=>{
  assert.equal(connectionPhase("qr_ready",1000,1000,45999),"qr_ready");
  assert.equal(connectionPhase("qr_ready",1000,1000,46000),"qr_expired");
  assert.equal(connectionPhase("connected",0,1000,70000),"connected");
});

test("pareamento recusado deve ser mostrado explicitamente",()=>{
  assert.equal(connectionPhase("pair_failed",0,0,100000),"pair_failed");
  assert.equal(connectionPhase("logged_out",0,0,100000),"logged_out");
});

test("estados nunca geram QR falso sem timestamp",()=>{
  assert.equal(connectionPhase("qr_ready",0,0,200),"qr_expired");
});


test("logout real (401) não reconecta até novo vínculo autorizado",()=>{
  assert.deepEqual(reconnectPlan(401,0),{retry:false,delayMs:null});
});

test("restart exigido após pareamento (515) reconecta rapidamente",()=>{
  assert.deepEqual(reconnectPlan(515,0),{retry:true,delayMs:500});
});

test("queda transitória gera backoff limitado e não força novo QR",()=>{
  assert.deepEqual(reconnectPlan(408,0),{retry:true,delayMs:2000});
  assert.deepEqual(reconnectPlan(408,1),{retry:true,delayMs:4000});
  assert.deepEqual(reconnectPlan(408,8),{retry:true,delayMs:30000});
});
