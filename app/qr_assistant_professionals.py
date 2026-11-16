"""Readable choices from caller-verified professionals; no booking authority."""
import re

from app.qr_assistant_catalog import clip
from app.qr_behaviors import norm
from app.qr_presentation import PresentationPreferences, escape_label


def professional_menu(people, *, page=0, preferences=None, information_only=False):
    size = 10 if len(people) <= 10 else 8
    pages = max(1, (len(people)+size-1)//size)
    page = max(0, min(page, pages-1))
    rows = people[page*size:(page+1)*size]
    prefs = PresentationPreferences.from_mapping(preferences)
    marker = '👤 ' if prefs.emoji == 'light' else ''
    heading = marker+'*Escolha o profissional*'
    intro = heading+'\nQuem você prefere para este atendimento?'
    if information_only:
        intro += '\nVou consultar os horários; nenhuma reserva será feita.'
    answer = heading+'\nQuem você prefere?\n\n'+'\n'.join(f"{index}. *{escape_label(clip(person['name'], 80))}*"
        for index, person in enumerate(rows, 1))
    answer += '\n\nResponda com o número ou o nome de quem prefere.'
    if information_only:
        answer += '\n_Consulta de horários, sem reserva._'
    if pages > 1:
        answer += f'\nPágina {page+1}/{pages} · {len(people)} profissionais'
        if page+1 < pages:
            answer += '\nPeça mais profissionais para ver as próximas opções.'
        if page:
            answer += '\nVocê também pode voltar aos profissionais anteriores.'
    return {'rows': rows, 'page': page, 'pages': pages, 'total': len(people),
            'intro': intro, 'answer': answer}


def requested_page(body, page):
    value = norm(body).strip(' .!?')
    value = re.sub(r'^(?:pode|poderia)\s+(?:me\s+)?mostrar\s+(?:os\s+)?', '', value)
    if value in {'profissionais', 'ver profissionais', 'opcoes de profissionais',
                 'ver opcoes de profissionais', 'quero ver os profissionais'}:
        return page
    explicit = re.fullmatch(r'(?:ver )?profissionais pagina (\d{1,3})', value)
    if explicit:
        return max(0, int(explicit[1])-1)
    if value in {'mais profissionais', 'ver mais profissionais', 'outros profissionais'}:
        return page+1
    if value in {'profissionais anteriores', 'voltar aos profissionais anteriores'}:
        return max(0, page-1)
    return None
