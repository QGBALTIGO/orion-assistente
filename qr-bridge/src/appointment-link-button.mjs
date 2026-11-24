import {randomUUID} from 'node:crypto';
import {validateAssistantPresentation} from './assistant-presentation.mjs';

const links = [
  {
    heading: '🔗 *Gerencie seu agendamento:*',
    label: 'Ver horário',
    oldInstruction: '_Precisa cancelar ou remarcar? Utilize o link acima com até 1 hora de antecedência._',
    instruction: '_Você pode cancelar ou remarcar pelo botão com até 1 hora de antecedência._',
  },
  {
    heading: '🔗 *Deixe sua avaliação:*',
    label: 'Avaliar',
  },
];

// Only the two published transactional templates are eligible. The caller
// must already have authenticated the reply or obtained the outbox grant.
export function appointmentLinkButton(text) {
  const paragraphs = text.trim().split('\n\n');
  const matches = paragraphs.flatMap((paragraph, index) => {
    const [heading, url, ...rest] = paragraph.split('\n');
    const action = links.find(item => item.heading === heading);
    return action && url && !rest.length ? [{index, url, ...action}] : [];
  });
  if (matches.length !== 1) return null;

  const {index, url, label, oldInstruction, instruction} = matches[0];
  paragraphs.splice(index, 1);
  const body = paragraphs.map(paragraph =>
    paragraph === oldInstruction ? instruction : paragraph).join('\n\n');
  try {
    const {action, id, version} = validateAssistantPresentation({
      version: 1,
      id: randomUUID().replaceAll('-', ''),
      action: {type: 'url_button', purpose: 'appointment', text: body, button_text: label, url},
    });
    return {action, id, version};
  } catch {
    // Keep the original text when the appointment link or message is invalid.
    return null;
  }
}
