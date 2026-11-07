/** A scoped native-list pilot never enables carousels or another business.
 * The existing global opt-in remains available, with the same semantics. */
import {channelId} from './channel-storage.mjs';

export function chatbotInteractiveMode(action,{tenantId,deviceId,env=process.env}={}){
  if(env.QR_NATIVE_INTERACTIVE_ENABLED==='1')return 'native';
  if(action?.type!=='list'||!channelId(tenantId)||!channelId(deviceId))return 'text_fallback';
  const raw=env.QR_NATIVE_LIST_SCOPES;
  if(typeof raw!=='string'||!raw.length||raw.length>10000)return 'text_fallback';
  const entries=raw.split(',').map(value=>value.trim());
  if(entries.length>128)return 'text_fallback';
  const scopes=[];
  for(const entry of entries){
    const parts=entry.split(':');
    // Reject the whole malformed configuration; never accept wildcards,
    // partial UUIDs or an isolated device ID without its tenant binding.
    if(parts.length!==2||!parts.every(channelId))return 'text_fallback';
    scopes.push(entry);
  }
  return scopes.includes(tenantId+':'+deviceId)?'native':'text_fallback';
}
