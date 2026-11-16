/* Browser-only synthetic fixture. Never loaded by the application. */
window.mountPerformanceFixture=effectivePlan=>{
  const tenant='10000000-0000-4000-8000-000000000001',professional='51000000-0000-4000-8000-000000000001';
  const today=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const start=today.slice(0,8)+'01',end=new Date(Date.UTC(Number(today.slice(0,4)),Number(today.slice(5,7)),0,12)).toISOString().slice(0,10);
  const pros=[{id:professional,name:'Peixoto',active:true},{id:'51000000-0000-4000-8000-000000000002',name:'Guilherme',active:true},{id:'51000000-0000-4000-8000-000000000003',name:'Lucas',active:true}];
  let goals=[{id:'52000000-0000-4000-8000-000000000001',version:1,template_version:1,cycle_id:'52000000-0000-4000-8000-000000000001:'+today.slice(0,7),recurrence:'monthly',scope:'company',professional_id:null,metric:'revenue_cents',target_value:500000,actual_value:175000,progress_percent:35,start_date:start,end_date:end,active:true,can_edit:true,is_current_cycle:true},
    {id:'52000000-0000-4000-8000-000000000002',version:1,template_version:1,cycle_id:'52000000-0000-4000-8000-000000000002:'+today.slice(0,7),recurrence:'monthly',scope:'professional',professional_id:pros[1].id,professional_name:'Guilherme',metric:'revenue_cents',target_value:100000,actual_value:7500,progress_percent:7.5,start_date:start,end_date:end,active:true,can_edit:true,is_current_cycle:true}];
  const states=['completed','cancelled','confirmed','no_show','hold','expired_hold'];
  const names=['Kayky Sousa','Ana Carolina de Albuquerque e Vasconcelos','Marcos Oliveira','Carlos Souza','Rafael Lima','Cliente excluído'];
  const appointments=Array.from({length:26},(_,i)=>({id:'53000000-0000-4000-8000-'+String(i+1).padStart(12,'0'),protocol:'AG-EXEMPLO'+(i+1),professional_id:professional,professional_name:'Peixoto',customer_name:names[i%6],customer_phone:i%6===5?null:'+5511999990000',customer_deleted:i%6===5,status:states[i%6]==='expired_hold'?'cancelled':states[i%6],history_status:states[i%6],start_at:today+'T12:00:00Z',end_at:today+'T13:00:00Z',hold_expires_at:'2099-12-31T23:59:59Z',total_cents:7500,original_total_cents:8000,discount_cents:i%6===0?500:0,price_mode:'fixed',items:[{service_id:'service1',name_snapshot:i===1?'Corte com acabamento e alinhamento térmico':'Corte',duration_snapshot_min:40,price_snapshot_cents:5000,price_mode_snapshot:'fixed'},{service_id:'service2',name_snapshot:'Barba',duration_snapshot_min:20,price_snapshot_cents:3000,price_mode_snapshot:'fixed'}],cancellation:i%6===1?{event:'cancelled',requested_by:'customer',reason:'Cliente pediu para cancelar por um imprevisto.',occurred_at:today+'T10:00:00Z'}:i%6===5?{event:'hold_expired',occurred_at:today+'T10:30:00Z'}:null}));
  window.performanceFixtureCalls=[];
  const request=async(path,method='GET',data)=>{
    window.performanceFixtureCalls.push({path,method,data});
    if(path.endsWith('/management/permissions')&&method==='GET')return {effective_plan:effectivePlan};
    const url=new URL(path,'https://orion-fixture.invalid'),params=url.searchParams;
    if(path.includes('/performance/professionals/')){
      const filtered=appointments.filter(a=>!params.get('status')||a.history_status===params.get('status'));
      const offset=Number(params.get('offset')||0),limit=Number(params.get('limit')||25),p=pros.find(p=>url.pathname.includes(p.id));
      return {professional:p,period:{start_date:params.get('start_date'),end_date:params.get('end_date'),timezone:'America/Sao_Paulo'},items:filtered.slice(offset,offset+limit).map(a=>({...a,professional_id:p.id,professional_name:p.name})),total:filtered.length,limit,offset,has_more:offset+limit<filtered.length,counts:{all:appointments.length,...Object.fromEntries(states.map(s=>[s,appointments.filter(a=>a.history_status===s).length]))}};
    }
    if(path.includes('/goals')){
      if(method==='POST'){const record={...goals[0],...data,id:'new-goal',cycle_id:'new-goal:'+today.slice(0,7),actual_value:0,progress_percent:0};goals.push(record);return record;}
      if(method==='PATCH'){const index=goals.findIndex(g=>url.pathname.endsWith('/'+g.id));goals[index]={...goals[index],...data};return goals[index];}
      if(method==='DELETE'){goals=goals.filter(g=>!url.pathname.endsWith('/'+g.id));return {};}
      return {items:goals,templates:goals};
    }
    if(path.includes('/performance/daily'))return {items:pros.map((p,i)=>({date:today,professional_id:p.id,professional_name:p.name,completed:3-i,revenue_cents:i===0?100000:i===1?7500:67500,commission_cents:i===0?10000:i===1?750:6750}))};
    if(path.includes('/performance?'))return {items:pros.map((p,i)=>({...p,completed:3-i,cancelled:i===0?1:0,revenue_cents:i===0?100000:i===1?7500:67500}))};
    if(path.includes('/finance/commissions'))return {items:pros.map((p,i)=>({...p,commission_mode:'percent',commission_percent:10,earned_cents:i===0?10000:i===1?750:6750,advances_cents:0,payable_cents:i===0?10000:i===1?750:6750}))};
    if(path.endsWith('/professionals'))return pros;

    if(path.includes('/management/customers'))return {items:[
      {id:'c1',name:'Ana Carolina',active:true,phone:'+5567991234567',total_appointments:8,total_spent_cents:40000,loyalty_points:8},
      {id:'c2',name:'Carlos Eduardo',active:true,phone:'+5567992345678',total_appointments:4,total_spent_cents:20000,loyalty_points:4},
      {id:'c3',name:'Mariana Souza',active:true,phone:'+5567993456789',total_appointments:6,total_spent_cents:30000,loyalty_points:6}],total:3};
    if(path.includes('/management/finance/summary'))return {summary:{income_cents:175000,expense_cents:30000,balance_cents:145000,count:6,average_ticket_cents:29167,by_category:[]},daily:[{date:today,income_cents:175000,expense_cents:30000}]};
    if(path.includes('/management/finance?'))return {items:[{id:'f1',kind:'income',description:'Atendimentos',amount_cents:175000,category:'Serviços',status:'paid',occurred_at:today+'T12:00:00Z',payment_method:'pix',version:1},{id:'f2',kind:'expense',description:'Produtos e materiais',amount_cents:30000,category:'Materiais',status:'paid',occurred_at:today+'T12:00:00Z',payment_method:'pix',version:1}],total:2};
    return {items:[],total:0};
  };
  window.OrionSuite.mount({user:{role:'member',name:'Conta de demonstração',email:'synthetic@example.invalid'},tenants:[{id:tenant,name:'Barbearia · prévia',slug:'synthetic',timezone:'America/Sao_Paulo',role:'owner'}],tenantId:tenant,initialRoute:'desempenho',request,openLegacy(){},onRouteChange(){}});
};
