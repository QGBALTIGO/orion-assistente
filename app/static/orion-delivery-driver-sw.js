// The offline screen contains no orders, credentials, locations or customer data.
self.addEventListener('fetch',event=>{
  if(event.request.mode!=='navigate')return;
  event.respondWith(fetch(event.request).catch(()=>new Response(
    '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Orion Entregador</title><h1>Sem conexão</h1><p>Reconecte para consultar suas entregas e confirmar a operação no servidor.</p><a href="./">Tentar novamente</a></html>',
    {status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}})));
});
