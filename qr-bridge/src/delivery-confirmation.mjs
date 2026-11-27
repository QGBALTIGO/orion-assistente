/** Only a current authenticated Delivery send_text grant can carry buttons. */
const suffix='\nResponda *CONFIRMO* para registrar o pedido ou *NÃO* para cancelar o resumo.';
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
export function deliveryPresentationFromPermit(permit,snapshot){
  if(permit?.authorized!==true||permit.type!=='send_text'||permit.purpose!=='delivery_reply'||
      snapshot?.capabilities?.delivery_reply_buttons!==1||!plain(permit.payload))return null;
  const {text,delivery_presentation:p}=permit.payload;
  if(!plain(p)||p.version!==1||typeof p.id!=='string'||!/^[a-f0-9]{32}$/.test(p.id)||
      !plain(p.action)||p.action.type!=='reply_buttons'||typeof p.action.text!=='string'||
      !p.action.text.trim()||p.action.text.length>1024||typeof text!=='string'||
      text!==p.action.text+suffix||!Array.isArray(p.action.buttons)||p.action.buttons.length!==2)return null;
  if(p.action.buttons.some((button,i)=>!plain(button)||button.id!=='oa1_'+p.id+'_'+i||
      button.title!==['Confirmar pedido','Cancelar'][i]))return null;
  return p;
}
