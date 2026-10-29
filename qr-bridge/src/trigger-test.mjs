// Isolated, opt-in WhatsApp reply pilot. Never send to arbitrary customers.
export const TRIGGER = 'ORIONTESTE';
export function testReplyFor({ text, phone, allowedPhone, fromMe = false }) {
  const digits = value => String(value || '').replace(/\D/g, '');
  if (fromMe || !allowedPhone || digits(phone) !== digits(allowedPhone)) return null;
  if (typeof text !== 'string' || text.trim().toUpperCase() !== TRIGGER) return null;
  return [
    '🧪 *TESTE ORION — AGENDAMENTO FICTÍCIO*',
    '',
    'Olá! Esta é uma simulação de confirmação. Nenhum agendamento real foi criado.',
    '',
    '✂️ Serviço: Corte masculino',
    '📆 Data: 30/10/2026',
    '🕒 Horário: 14h30',
    '💈 Profissional: Peixoto',
    '',
    '🔗 *VER AGENDAMENTO*',
    'https://orionagenda.pro',
    '',
    'Mensagem automática de teste do Orion.'
  ].join('\n');
}
