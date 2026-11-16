/** Per-message text lets the API exclude completed IDs from a replayed batch
 * without dropping fresh customer input or interpreting old input twice. */
export function assistantParts(parts,ids,text){
  if(parts===undefined)return undefined;
  if(!Array.isArray(parts)||!Array.isArray(ids)||parts.length!==ids.length||parts.length<1||parts.length>6||
      parts.some((part,index)=>!part||typeof part!=='object'||Array.isArray(part)||
        Object.keys(part).some(name=>!['message_id','text'].includes(name))||
        part.message_id!==ids[index]||typeof part.text!=='string'||!part.text.trim()||part.text.length>1500)||
      parts.map(part=>part.text).join('\n')!==text)throw Error('invalid_message_parts');
  return parts.map(part=>({message_id:part.message_id,text:part.text}));
}
