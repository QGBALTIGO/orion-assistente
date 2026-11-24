import test from 'node:test';
import assert from 'node:assert/strict';
import {proto} from '@whiskeysockets/baileys';
import {appointmentLinkButton} from '../src/appointment-link-button.mjs';
import {buildAssistantPresentation} from '../src/assistant-presentation.mjs';

const link = 'https://orionagenda.pro/AG-X2FL6Z#an_example_scoped_token';
const confirmation = [
  'Olá! Seu agendamento foi confirmado.',
  '🔗 *Gerencie seu agendamento:*\n' + link,
  '_Precisa cancelar ou remarcar? Utilize o link acima com até 1 hora de antecedência._',
  'Orion Agenda',
].join('\n\n');
const review = [
  '⭐ *Que tal avaliar sua experiência?*',
  '🔗 *Deixe sua avaliação:*\n' + link,
  'Muito obrigado pela preferência!',
].join('\n\n');

for (const [label, text] of [['Ver horário', confirmation], ['Avaliar', review]]) {
  test('builds an authenticated appointment URL action with the label ' + label, async () => {
    const presentation = appointmentLinkButton(text);
    const result = await buildAssistantPresentation(presentation, {sdk: {proto}, interactiveMode: 'native'});
    const wire = proto.Message.decode(proto.Message.encode(result.content).finish()).interactiveMessage;
    const [button] = wire.nativeFlowMessage.buttons;
    assert.equal(button.name, 'cta_url');
    assert.deepEqual(JSON.parse(button.buttonParamsJson), {display_text: label, url: link});
    assert.equal(wire.body.text.includes(link), false);
    assert.equal(wire.body.text.includes('link acima'), false);
    assert.equal(wire.body.text.includes('pelo botão'), label === 'Ver horário');
    assert.equal(result.relayOptions.additionalNodes[0].tag, 'biz');
    assert.deepEqual(result.selectionIds, []);
  });
}

test('keeps ordinary reminders, unknown destinations and ambiguous actions as plain text', () => {
  assert.equal(appointmentLinkButton('Seu atendimento começa em uma hora.\n\n' + link), null);
  assert.equal(appointmentLinkButton(review.replace('orionagenda.pro', 'orionagenda.test')), null);
  assert.equal(appointmentLinkButton(review.replace('#an_example_scoped_token', '')), null);
  assert.equal(appointmentLinkButton(review.replace('/AG-X2FL6Z', '/different-page')), null);
  assert.equal(appointmentLinkButton(review + '\n\n' + confirmation), null);
});
