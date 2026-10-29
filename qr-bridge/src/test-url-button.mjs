// Explicitly isolated experimental URL-button payload for the allowlisted pilot.
// This only verifies our payload contract, not rendering by a WhatsApp client.
export function makeTestButtonPayload(text) {
  if (typeof text !== 'string' || !text.includes('🔗 *VER AGENDAMENTO*')) throw Error('invalid_test_reply');
  return {
    text:text.split('🔗 *VER AGENDAMENTO*')[0].trim(),
    footer:'Orion • Demonstração',
    templateButtons:[{index:1,urlButton:{displayText:'VER AGENDAMENTO',url:'https://orionagenda.pro'}}]
  };
}
