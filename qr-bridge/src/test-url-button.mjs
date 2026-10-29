// QR-linked WhatsApp Web does not reliably render URL buttons.
// Keep the URL in the QR fallback; use Meta's documented Cloud API CTA
// only if explicitly configured for the authorized pilot.
export const TEST_CTA_URL = 'https://orionagenda.pro';
export const TEST_CTA_LABEL = 'VER AGENDAMENTO';
const MARKER = '🔗 *VER AGENDAMENTO*';

function testBody(text) {
  if (typeof text !== 'string' || !text.includes(MARKER)) {
    throw Error('invalid_test_reply');
  }
  const body = text.split(MARKER)[0].trim();
  if (!body || body.length > 1024) throw Error('invalid_test_body');
  return body;
}

export function makeTestFallbackText(text) {
  const body = testBody(text);
  return `${body}\n\n🔗 *${TEST_CTA_LABEL}*\n${TEST_CTA_URL}\n\nMensagem automática de teste do Orion.`;
}

export function makeTestCloudCtaPayload(phone, text) {
  if (typeof phone !== 'string' || !/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone)) {
    throw Error('invalid_test_recipient');
  }
  return {
    messaging_product: 'whatsapp',
    recipient_type: 'individual',
    to: phone,
    type: 'interactive',
    interactive: {
      type: 'cta_url',
      body: { text: testBody(text) },
      action: {
        name: 'cta_url',
        parameters: { display_text: TEST_CTA_LABEL, url: TEST_CTA_URL }
      }
    }
  };
}

export async function sendTestCloudCta({phone, text, accessToken, phoneNumberId,
  apiVersion = 'v25.0', fetchImpl = fetch}) {
  const payload = makeTestCloudCtaPayload(phone, text);
  if (typeof accessToken !== 'string' || !accessToken.trim() ||
      typeof phoneNumberId !== 'string' || !/^\d{5,30}$/.test(phoneNumberId) ||
      !/^v\d{1,2}\.\d{1,2}$/.test(apiVersion)) {
    throw Error('cloud_cta_not_configured');
  }
  const endpoint = `https://graph.facebook.com/${apiVersion}/${phoneNumberId}/messages`;
  const response = await fetchImpl(endpoint, {
    method: 'POST', redirect: 'error',
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(10000)
  });
  // Meta may return identifiers: never log response bodies.
  if (!response.ok) return { accepted: false, transport: 'meta', httpStatus: response.status };
  const body = await response.json();
  const id = body?.messages?.[0]?.id;
  if (typeof id !== 'string' || !/^wamid\.[\w=+\-/]{5,512}$/.test(id)) {
    return { accepted: false, transport: 'meta', httpStatus: response.status };
  }
  // Acceptance is not proof the recipient saw/rendered the button.
  return { accepted: true, transport: 'meta', delivery: 'unconfirmed', id };
}
