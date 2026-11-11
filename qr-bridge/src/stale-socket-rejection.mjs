// Baileys may start a retry just before a socket is retired and leave its
// rejected retry promise unobserved. Keep the auth fence while preventing
// that specific stale callback from terminating every channel in the bridge.
export class StaleSocketRejection extends Error {
  constructor(){super('channel_socket_replaced');this.name='StaleSocketRejection';}
}

export function installStaleSocketRejectionGuard(target=process){
  const listener=reason=>{
    if(reason instanceof StaleSocketRejection)return;
    // Installing a listener disables Node's default fatal handling. Preserve
    // it for every other rejection, including auth and storage failures.
    throw reason instanceof Error?reason:Error('unhandled_rejection');
  };
  target.on('unhandledRejection',listener);
  return ()=>target.off('unhandledRejection',listener);
}
