/**
 * Conservative, one-message diagnostic mode for private WhatsApp contacts.
 * The operator must request it on the current send; it is not remembered.
 * It only skips Baileys' cached device-list lookup. It does NOT force a
 * Signal session reset, relink, resend, or a change from PN to LID.
 */
export function chooseFreshDeviceLookup(requested, {reliableSendEnabled=false, globalDefault=false}={}) {
  if(typeof requested!=="boolean")throw Error("Opção de dispositivos inválida");
  if(requested && !reliableSendEnabled){
    throw Error("Modo de teste indisponível: envio confiável não habilitado");
  }
  return reliableSendEnabled && (requested || globalDefault);
}
