/** Business stanza hints for the typed native messages we actually support.
 * Uses Baileys' public additionalNodes option; does not modify Signal or
 * accept arbitrary nodes from a template, webhook, URL response or caller.
 * Reference: Evolution API 2.4.0-rc1 interactive-message interoperability notes.
 */
export function nativeRelayOptions(message){
  const content=message?.viewOnceMessage?.message||message;
  if(content?.listMessage){
    return {additionalNodes:[{tag:'biz',attrs:{},content:[{tag:'list',attrs:{type:'product_list',v:'2'}}]}]};
  }
  if(content?.interactiveMessage?.carouselMessage){
    return {additionalNodes:[{tag:'biz',attrs:{},content:[{tag:'interactive',attrs:{type:'native_flow',v:'1'},
      content:[{tag:'native_flow',attrs:{v:'9',name:'mixed'}}]}]}]};
  }
  return {};
}
