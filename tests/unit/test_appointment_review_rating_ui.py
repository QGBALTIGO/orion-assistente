"""The Orion post-service review is intentionally short and rating-first.

Other appointment statuses must retain the full receipt, preserving clients'
ability to see their protocol and address when changing an appointment.
"""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]/"app"/"static"
JS=(ROOT/"orion-appointment.js").read_text(encoding="utf-8")
CSS=(ROOT/"orion-appointment.css").read_text(encoding="utf-8")
HTML=(ROOT/"orion-appointment.html").read_text(encoding="utf-8")


def test_review_only_has_three_compact_facts_and_other_receipts_stay_full():
    assert "data.status==='completed' && data.can_review===true" in JS
    summary=JS.split("const facts=reviewMode?",1)[1].split(";",1)[0]
    abbreviated,full=summary.split(":\n",1)
    for label in ("Serviço","Profissional","Data e horário"):
        assert f"['{label}'" in abbreviated
    for label in ("Cliente","Duração","Protocolo","Endereço"):
        assert f"['{label}'" not in abbreviated
        assert f"['{label}'" in full
    assert "classList.toggle('review-mode',reviewMode)" in JS
    assert "if(!reviewMode){" in JS
    assert "#review{order:1" in CSS
    assert "#actions{order:2" in CSS


def test_radio_stars_fill_all_lower_scores_and_keep_screen_reader_labels():
    assert "for(let n=1;n<=5;n++)" in JS
    assert "input.type='radio';input.name='rating'" in JS
    assert "index<filled" in JS
    assert "index+1===selected" in JS
    assert "input.addEventListener('change',()=>paintRating())" in JS
    assert "pointerType==='mouse'" in JS
    assert "stars.addEventListener('pointerleave',()=>paintRating())" in JS
    assert "aria-describedby','rating-feedback'" in JS
    assert "5 de 5 estrelas" not in JS # feedback is generated from the actual score
    assert 'id="rating-feedback"' in HTML
    assert 'aria-live="polite"' in HTML
    assert "#review .stars label.is-filled span" in CSS
    assert "color:var(--primary)" in CSS
    assert "min-width:0" in CSS


def test_original_review_submission_and_contact_options_remain():
    assert "request('review',{rating:Number(chosen.value),comment:$('comment').value})" in JS
    assert "document.querySelector('input[name=rating]:checked')" in JS
    assert "data.can_change" in JS
    assert "Falar com o Orion" in JS
    assert "Agendar novamente" in JS
    assert "#review .stars input:focus-visible+span" in CSS
    assert "@media(max-width:359px)" in CSS


def test_no_customer_text_inserted_as_html():
    assert "row.append(node('dt',key),node('dd',value))" in JS
    assert "ratingFeedback.textContent=" in JS
    assert "innerHTML" not in JS
