/* Synthetic bridge shared by the native-field regression. No production fetch. */
({effectivePlan}) => {
  window.OrionFields.bind(document);
  const tenant='10000000-0000-4000-8000-000000000001';
  const professionalId='20000000-0000-4000-8000-000000000001';
  const serviceId='30000000-0000-4000-8000-000000000001';
  const customer={id:'40000000-0000-4000-8000-000000000001',name:"Ana D'Ávila-Souza",
    phone:'+5555991234567',email:'ana@example.invalid',active:true,version:1,
    birth_date:'2000-02-29',created_at:'2026-10-17T13:12:00Z',completed_appointments:0};
  const professionals=[{id:professionalId,name:'Profissional sintético',specialty:'Cabelo e barba',
    phone_e164:'+5567991234564',active:true,accepts_appointments:true,color:'#31ad52',
    commission_mode:'percent',commission_percent:25,commission_config_version:1}];
  const services=[{id:serviceId,name:'Serviço sintético',duration_min:40,price_cents:3500,
    price_mode:'fixed',active:true,catalog_kind:'base',color:'#2e7bf6',prep_min:0,cleanup_min:0}];
  const links=[{professional_id:professionalId,service_id:serviceId}];
  const preference={version:1,can_save:true,business:{contact_phone:'+5567991234564',
    monthly_goal_cents:100000,tax_document_kind:'none',tax_document:null,
    trade_name:'Empresa sintética',legal_name:'Empresa sintética',google_review_url:null,
    google_review_message:'Obrigado pela visita.',auto_review_requested:false,weekly_summary_requested:false}};
  const site={title:'Empresa sintética',description:'Atendimento sintético',slug:'empresa-sintetica',
    version:1,theme_color:'#2e7bf6',show_prices:true,contact_phone:'+5567991234564',
    whatsapp_phone:null,address:null,address_number:null,address_no_number:false,
    address_complement:null,neighborhood:null,city:null,state:null,postal_code:null};
  const rule={id:'50000000-0000-4000-8000-000000000001',version:1,name:'Programa sintético',
    active:true,earning_mode:'both',points_per_step:1,points_per_visit:1,spend_step_cents:1000,
    reward_points:10,reward_type:'percent',reward_percent:10,reward_valid_days:30};
  const writes=[];
  window.__fieldsQA={writes,tenant,professionalId,serviceId};
  window.fetch=async input=>{
    const url=new URL(String(input),location.href);
    if(url.hostname==='viacep.com.br'&&url.pathname==='/ws/01001000/json/')
      return new Response(JSON.stringify({cep:'01001-000',logradouro:'Praça da Sé',bairro:'Sé',localidade:'São Paulo',uf:'SP',complemento:''}),{status:200,headers:{'Content-Type':'application/json'}});
    throw Error('Unexpected fetch in isolated fields fixture: '+url.pathname);
  };
  const request=async(path,method='GET',data)=>{
    if(path.endsWith('/management/permissions')&&method==='GET')return {effective_plan:effectivePlan};
    if(method!=='GET'){
      writes.push({path,method,data:structuredClone(data)});
      if(path.includes('/customers/'))return Object.assign(customer,data,{version:customer.version+1});
      if(path.endsWith('/preferences'))return Object.assign(preference,{version:preference.version+1,business:{...preference.business,...data.business}});
      if(path.endsWith('/site'))return Object.assign(site,data,{version:site.version+1});
      return {id:crypto.randomUUID(),...data,version:2};
    }
    if(path.includes('/management/customers'))return {items:[customer],total:1};
    if(path.endsWith('/professionals'))return professionals;
    if(path.endsWith('/services'))return services;
    if(path.endsWith('/professional_services'))return links;
    if(path.endsWith('/management/professionals/commission-rules'))return {items:[]};
    if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[]};
    if(path.endsWith('/management/preferences'))return preference;
    if(path.endsWith('/management/site'))return site;
    if(path.endsWith('/management/site/publication'))return {state:'ready',can_edit:true,can_publish:true,ready:true,issues:[],draft_version:site.version,history:[]};
    if(path.includes('/management/site/metrics'))return {daily:[],visits:0};
    if(path.includes('/management/site/slug-availability'))return {slug:new URL(path,location.href).searchParams.get('slug'),available:true,suggestions:[]};
    if(path.endsWith('/management/loyalty'))return {rules:[rule],summary:{}};
    if(path.includes('/management/loyalty/ranking'))return {items:[],total:0};
    if(path.includes('/management/finance/summary'))return {summary:{income_cents:0,expense_cents:0,balance_cents:0},daily:[]};
    if(path.includes('/management/finance/dre'))return {summary:{}};
    if(path.includes('/management/finance'))return {items:[],total:0};
    if(path.includes('/management/service-packages'))return {items:[]};
    if(path.endsWith('/ai/status'))return {configured:false,enabled:false};
    if(path.includes('/management/goals'))return {items:[],templates:[]};
    if(path.includes('/management/performance'))return {items:[]};
    return [];
  };
  window.OrionSuite.mount({user:{role:'member',name:'Conta sintética',email:'preview@example.invalid'},
    tenants:[{id:tenant,name:'Empresa sintética',slug:'empresa-sintetica',timezone:'America/Sao_Paulo',role:'owner'}],
    tenantId:tenant,businessContextReady:true,initialRoute:'clientes',request,openLegacy(){},onRouteChange(){}});
}
