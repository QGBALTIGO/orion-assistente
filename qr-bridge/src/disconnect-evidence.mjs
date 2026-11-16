/** Pinned SDK6.7.24 structured disconnect evidence only. Never serialize an
 * exception, message, stack, binary node, provider attributes, or address.
 * socket.js507-515 preserves a stream node/provider reason in Boom.data;
 * socket.js602-604 preserves the original WebSocket Error there. A bare500
 * has no evidence of a damaged Signal/auth session and remains unknown. */
const sources=new Set(['stream_error','provider_failure','websocket_error','sdk_status','unknown']);
const streamConditions=new Set(['conflict','restart_required']);
const websocketCodes=new Set(['ECONNRESET','ECONNREFUSED','ETIMEDOUT','ETIMEOUT','ENOTFOUND','EAI_AGAIN','EPIPE',
  'ENETUNREACH','EHOSTUNREACH','WS_ERR_INVALID_OPCODE','WS_ERR_INVALID_UTF8','WS_ERR_INVALID_CLOSE_CODE',
  'WS_ERR_UNEXPECTED_RSV_1','WS_ERR_UNEXPECTED_RSV_2_3','WS_ERR_EXPECTED_FIN','WS_ERR_EXPECTED_MASK',
  'WS_ERR_UNEXPECTED_MASK','WS_ERR_INVALID_CONTROL_PAYLOAD_LENGTH','WS_ERR_UNSUPPORTED_DATA_PAYLOAD_LENGTH',
  'WS_ERR_UNSUPPORTED_MESSAGE_LENGTH','WS_ERR_TOO_MANY_BUFFERED_PARTS']);
const numeric=value=>Number.isSafeInteger(value)&&value>=100&&value<=999?value:
  typeof value==='string'&&/^[1-9][0-9]{2}$/.test(value)?Number(value):null;
const unknown=()=>({source:'unknown',condition:'unknown',provider_reason_numeric:null,websocket_code:null});

/** Defense in depth at the log/health boundary; never spread a caller object. */
export function safeDisconnectEvidence(value){
  const result=unknown();
  try{
    const source=value?.source;
    if(!sources.has(source))return result;
    result.source=source;
    const condition=value.condition;
    if(source==='stream_error'&&streamConditions.has(condition))result.condition=condition;
    if(source==='sdk_status'&&['timeout_or_lost','connection_closed'].includes(condition))result.condition=condition;
    if(['stream_error','provider_failure'].includes(source))result.provider_reason_numeric=numeric(value.provider_reason_numeric);
    const websocketCode=source==='websocket_error'?value.websocket_code:null;
    if(websocketCodes.has(websocketCode))result.websocket_code=websocketCode;
  }catch{return unknown();}
  return result;
}

export function disconnectEvidence(error){
  try{
    if(error?.isBoom!==true)return unknown();
    const code=numeric(error.output?.statusCode),data=error.data;
    if(data?.tag==='stream:error'&&data.attrs&&typeof data.attrs==='object'){
      const first=Array.isArray(data.content)?data.content[0]:null;
      return safeDisconnectEvidence({source:'stream_error',condition:code===515?'restart_required':first?.tag,
        provider_reason_numeric:numeric(data.attrs.code)});
    }
    // In this SDK the only Boom.data=Error disconnect path is its public
    // WebSocket mapper. Unknown native codes stay null; messages are unread.
    if(data instanceof Error)return safeDisconnectEvidence({source:'websocket_error',websocket_code:data.code});
    const providerReason=numeric(data?.reason);
    if(providerReason!==null&&providerReason===code)return safeDisconnectEvidence({source:'provider_failure',provider_reason_numeric:providerReason});
    // These status enums combine several SDK sites. Do not pretend to know
    // which close/keepalive/query/QR deadline fired without structured proof.
    if(code===408)return safeDisconnectEvidence({source:'sdk_status',condition:'timeout_or_lost'});
    if(code===428)return safeDisconnectEvidence({source:'sdk_status',condition:'connection_closed'});
  }catch{/* Malformed/hostile exception access cannot affect recovery. */}
  return unknown();
}
