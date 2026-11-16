window.mountReceiptFixture=effectivePlan=>{
 const tenant='10000000-0000-4000-8000-000000000001',entryId='61000000-0000-4000-8000-000000000001',appointmentId='62000000-0000-4000-8000-000000000001';
 const now=new Date(),today=now.toISOString().slice(0,10),past=new Date(now.getTime()-2*3600000).toISOString();
 const settings={mode:'manual',version:0,can_save:true};
 const appointment={id:appointmentId,protocol:'AG-TESTE',status:'completed',customer_name:'Ana Carolina',customer_phone:'+5567991234567',professional_name:'Peixoto',professional_id:'51000000-0000-4000-8000-000000000001',start_at:past,end_at:new Date(now.getTime()-3600000).toISOString(),total_cents:5000,created_at:past,updated_at:past,allowed_actions:[],timezone:'America/Sao_Paulo',items:[{name_snapshot:'Corte masculino',duration_snapshot_min:40,price_snapshot_cents:5000,price_mode_snapshot:'fixed'}],history:[]};
 const row={id:entryId,appointment_id:appointmentId,kind:'income',entry_type:'general',description:'Atendimento AG-TESTE',customer_name:'Ana Carolina',amount_cents:5000,version:1,status:'pending',service_receivable:true,occurred_at:past,payment_method:'other',keep_pending:false,receipt_history:[],appointment};
 window.receiptFixtureCalls=[];
 const request=async(path,method='GET',data)=>{
  receiptFixtureCalls.push({path,method,data});
  if(path.endsWith('/management/permissions'))return {effective_plan:effectivePlan};
  if(path.endsWith('/finance/receipts/settings')){if(method==='PUT')Object.assign(settings,{mode:data.mode,version:settings.version+1});return {...settings};}
  if(path.endsWith('/finance/receipts/summary'))return {count:row.status==='pending'?1:0,amount_cents:5000};
  if(path.includes('/finance/receipts/appointment/'))return {items:[{...row,receipt_policy_mode:settings.mode}],can_write:true};
  if(path.endsWith('/finance/receipts/'+entryId))return {...row,receipt_policy_mode:settings.mode};
  if(path.endsWith('/finance/'+entryId+'/receipt')){
   const previous={status:row.status,keep_pending:row.keep_pending};
   if(data.action==='confirm')Object.assign(row,{status:'paid',receipt_source:'manual',received_at:data.received_at,payment_method:data.payment_method});
   if(data.action==='hold')Object.assign(row,{keep_pending:true,auto_receipt_active:false});
   if(data.action==='resume')Object.assign(row,{keep_pending:false,auto_receipt_active:true,auto_receipt_due_at:new Date(now.getTime()+22*3600000).toISOString()});
   if(data.action==='reopen')Object.assign(row,{status:'pending',keep_pending:true,received_at:null,receipt_source:null,auto_receipt_active:false});
   row.version++;row.receipt_history.unshift({action:data.action,source:'manual',actor_name:'Conta de demonstração',actor_role:'owner',occurred_at:now.toISOString(),details:{previous}});return {...row};
  }
  if(path.includes('/finance/summary?'))return {summary:{income_cents:row.status==='paid'?5000:0,expense_cents:0,balance_cents:row.status==='paid'?5000:0,count:1,average_ticket_cents:5000},daily:[]};
  if(path.includes('/finance?'))return path.includes('entry_type=commission_payment')?{items:[],total:0}:{items:[{...row}],total:1};
  if(path.endsWith('/appointments/'+appointmentId))return {...appointment};
  if(path.includes('/appointments?'))return {items:[{...appointment}],total:1,limit:80,offset:0};
  if(path.endsWith('/professionals'))return [{id:appointment.professional_id,name:'Peixoto',active:true}];
  return {items:[],total:0};
 };
 window.OrionSuite.mount({user:{role:'member',name:'Conta de demonstração',email:'synthetic@example.invalid'},tenants:[{id:tenant,name:'Barbearia · prévia',slug:'synthetic',timezone:'America/Sao_Paulo',role:'owner'}],tenantId:tenant,initialRoute:'financeiro',request,openLegacy(){},onRouteChange(){}});
};
