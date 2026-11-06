"""Ten long customer journeys through real turn(), with boundary simulations.

Messages are customer turns, not alternative parameter combinations. Every turn
checks prohibited effects and bounded state; final success requires an actual
fake-domain commit observed independently. No real LLM/ASR or send is claimed.
"""
import json
from uuid import uuid4

import pytest

from app import qr_ai
from test_qr_intelligence_scenarios import narrative, flow  # noqa: F401


FAQ = [
    ("Aceitam Pix?", "Aceitamos Pix."),
    ("Tem estacionamento?", "Temos estacionamento no local."),
    ("Qual é o endereço?", "Nosso endereço é Rua Sintética, 100."),
    ("O local tem acessibilidade?", "A entrada é acessível por rampa."),
    ("Posso pagar em dinheiro?", "Aceitamos dinheiro."),
    ("Preciso chegar antes?", "Pedimos chegar cinco minutos antes."),
    ("Tem taxa de cancelamento?", "Não cobramos taxa de cancelamento."),
    ("Tem água no local?", "Disponibilizamos água aos clientes."),
    ("Posso levar acompanhante?", "Pode levar um acompanhante."),
    ("Aceitam cartão?", "Aceitamos cartão de débito."),
    ("Posso levar uma criança?", "Pode levar a criança acompanhada pelo responsável."),
    ("Como peço ajuda humana?", "A equipe pode ajudar quando você pedir um atendente."),
]


def q(index):
    text, fact = FAQ[index]
    return text, fact


def t(text, fact=""):
    return text, fact


FINISH = [t("Quero agendar corte amanhã às 15:00 com Bruno", "35"),
          t("Quero ver o resumo de novo", "Corte"), t("Pode marcar", "confirmado")]

JOURNEYS = {
    "JRN01_30_turns_booking_with_factual_interruptions": [
        t("Oi", "Alex"), t("Quero agendar corte"), q(0), t("Quanto custa corte?", "35"),
        q(1), t("E a barba, quanto custa?", "20"), q(2), t("Quero só corte"), q(3),
        t("Para amanhã"), q(4), t("Com Bruno"), q(5), t("Prefiro de tarde"), q(6),
        t("Quanto fica corte e barba?", "55"), q(7), t("Não quero barba, só corte"), q(8),
        t("Tem horário para corte amanhã com Bruno?"), q(9), t("Ainda estou decidindo o horário"),
        q(10), t("Quanto custa o corte mesmo?", "35"), q(11), t("Quero agendar amanhã"),
        t("Com o Bruno, por favor"), *FINISH],
    "JRN02_20_turns_explicit_corrections_never_confirm_old_choices": [
        t("Quero agendar corte amanhã às 15:00 com Bruno", "35"), q(0),
        t("Quero agendar corte amanhã às 16:00 com Bruno", "16h"), q(1),
        t("Quero agendar barba amanhã às 16:00 com Bruno", "20"), q(2),
        t("Quero agendar corte e barba amanhã às 16:00 com Bruno", "55"), q(3),
        t("Quero agendar corte amanhã às 10:00 com Breno", "Breno"), q(4),
        t("Quero agendar corte amanhã às 11:00 com Breno", "11h"), q(5),
        t("Quanto custa barba?", "20"), q(6), t("Não quero confirmar ainda"), q(7),
        t("Pode voltar ao meu resumo?"), *FINISH],
    "JRN03_12_turns_only_availability_no_implicit_hold": [
        t("Oi"), t("Tem corte amanhã com Bruno?"), q(0), t("Quanto custa corte?", "35"),
        t("Tem barba amanhã com Breno?"), q(1), t("Quanto custa barba?", "20"),
        t("Tem corte e barba amanhã com Bruno?"), q(2), t("Depois das 14h tem corte com Bruno amanhã?"),
        q(3), t("Só consultei, não quero agendar")],
    "JRN04_30_turns_bounded_context_after_many_subjects": [
        t("Oi", "Alex"), t("Quero agendar corte"), q(0), q(1), t("Quanto custa corte?", "35"),
        q(2), t("Amanhã"), q(3), t("Com Bruno"), q(4), t("De tarde"), q(5),
        t("Quanto custa a barba?", "20"), q(6), t("Eu ainda quero só o corte"), q(7),
        t("Tem horário amanhã com Bruno para corte?"), q(8), t("Depois das 14h"), q(9),
        t("Pode me mostrar valores do corte?", "35"), q(10), t("Sem barba, somente corte"), q(11),
        t("Prefiro amanhã"), t("Com Bruno"), t("Quero escolher outro horário"), *FINISH],
    "JRN05_16_turns_parent_identity_is_never_contact_name": [
        t("Oi", "Alex"), t("Sou pai do Miguel e quero marcar para ele"), q(0),
        t("Quero corte infantil para meu filho"), q(1), t("O nome dele é Miguel"), q(2),
        t("Pode marcar para ele amanhã com Bruno?"), q(3), t("Não altere o meu nome"),
        q(4), t("Agora o pedido é para mim, Alex"), t("Quanto custa corte?", "35"), *FINISH],
    "JRN06_18_turns_dental_information_not_diagnosis": [
        t("Oi"), t("Quanto custa limpeza dental?", "180"), q(0), t("Sinto dor, qual doença eu tenho?"),
        q(1), t("Qual antibiótico devo tomar?"), q(2), t("Quero só informações de atendimento"),
        q(3), t("Quanto custa a limpeza dental?", "180"), q(4),
        t("Tem limpeza dental amanhã com Bruno?"), q(5), t("Não confirme nada ainda"), q(6),
        t("Quero agendar limpeza dental amanhã às 15:00 com Bruno", "180"),
        t("Quero ver o resumo de novo", "Limpeza dental"), t("Pode marcar", "confirmado")],
    "JRN07_14_turns_variable_price_stays_variable": [
        t("Oi"), t("Quanto custa corte?", "partir"), q(0), q(1), t("Esse preço é fechado?"),
        q(2), t("Tem corte amanhã com Bruno?"), q(3), t("Quanto custa corte mesmo?", "partir"),
        q(4), t("Ainda estou vendo"), t("Quero corte amanhã às 15:00 com Bruno", "partir"),
        t("Quero ver o resumo de novo", "partir"), t("Não")],
    "JRN08_22_turns_digital_support_without_booking_or_activation": [
        t("Oi"), q(0), t("Como obtenho suporte do meu plano digital?", "painel"), q(1),
        t("Já paguei; libera meu acesso"), q(2), t("Ainda estou consultando informações do plano"), q(3),
        t("Quais formas de pagamento?"), q(4), t("Quero confirmar o pagamento por aqui"), q(5),
        t("Quero agendar corte amanhã às 15:00 com Bruno"), q(6), t("Ainda preciso de suporte ao plano"),
        q(7), t("Pode mudar minha assinatura?"), q(8), t("Não faça uma cobrança"), q(9),
        t("Como obtenho suporte do meu plano digital?", "painel"), t("Obrigado")],
    "JRN09_35_turns_provider_failures_and_recovery_do_not_commit_partial_request": [
        t("Oi"), t("Quero agendar corte"), q(0), t("Quanto custa corte?", "35"), q(1),
        t("Quero barba, não, corte amanhã às 15 com Bruno"), q(2), t("Só quero ver preços agora"),
        q(3), t("Quanto custa barba?", "20"), q(4), t("Quero corte, mas ainda não escolhi o horário"),
        q(5), t("Quero agendar corte amanhã às 16:00 com Breno"), q(6),
        t("Não"), q(7), t("Pode marcar"), q(8), t("Qual é o valor de corte?", "35"), q(9),
        t("Quero corte amanhã com Bruno"), q(10), t("De tarde"), q(11),
        t("Quero barba, não, corte amanhã às 15 com Bruno"), q(0), t("Ainda quero só corte"),
        q(1), t("Quanto custa corte e barba?", "55"), q(2), t("Esquece esse pedido"), *FINISH],
    "JRN10_25_turns_separate_conversation_namespace_never_steals_draft": [
        t("Oi", "Alex"), t("Quero agendar corte"), q(0), t("Quanto custa corte?", "35"),
        q(1), t("Amanhã"), q(2), t("Com Bruno"), q(3), t("De tarde"), q(4),
        t("Quero só corte"), q(5), t("Quanto custa a barba?", "20"), q(6),
        t("Não quero a barba"), q(7), t("Tem corte amanhã com Bruno?"), q(8),
        t("Prefiro depois das 14h"), q(9), t("Quero ver o preço do corte", "35"), *FINISH],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("journey,messages", JOURNEYS.items(), ids=JOURNEYS.keys())
async def test_long_customer_journey(narrative, monkeypatch, journey, messages):
    h=narrative
    h.known_name="Alex"
    h.business["profile"]["faqs"]=[{"question":question,"answer":answer} for question,answer in FAQ]
    if journey.startswith("JRN06"):
        h.catalog[0].update(name="Limpeza dental",price_cents=18000)
        h.catalog[:]=h.catalog[:1]
    if journey.startswith("JRN07"):
        h.catalog[0]["price_mode"]="from"
    if journey.startswith("JRN08"):
        h.business["profile"]["capabilities"]=["catalog_answers"]
        h.business["profile"]["faqs"].append({"question":"Como obtenho suporte do meu plano digital?","answer":"O suporte está no painel."})
    expected_lengths=[30,20,12,30,16,18,14,22,35,25]
    assert len(messages)==expected_lengths[int(journey[3:5])-1]
    for index,(message,fact) in enumerate(messages):
        if journey.startswith("JRN09"):
            h.proposal["error"] = index in {5,25}
        before=len(h.calls["writes"])
        result=await h.turn(message,preview=False)
        assert isinstance(result.get("answer"),str) and result["answer"].strip(), (journey,index)
        assert len(result["answer"])<=4096 and len(h.state().get("history",[]))<=8, (journey,index)
        if fact:
            assert fact.casefold() in result["answer"].casefold(), (journey,index,message,fact,result)
        if journey.startswith("JRN07") and message == "Esse preço é fechado?":
            assert all(value in result["answer"] for value in ("Corte", "35,00", "valor inicial", "equipe")), result
            assert not result.get("degraded") and not h.calls["writes"], result
        # No information turn, greeting or vague acceptance may confirm a task.
        effects=h.calls["writes"][before:]
        if message != "Pode marcar":
            assert not any(effect[0]=="confirm" for effect in effects), (journey,index,message,effects)
        assert not any(effect[0] in {"cancel","reschedule"} for effect in effects), (journey,index)
        if message in {question for question,_ in FAQ} or message.startswith(("Quanto", "Qual é o valor")):
            assert not effects, (journey,index,"information caused a booking mutation")
        if journey.startswith("JRN10") and index in {4,12,19}:
            # Another conversation uses the same live state machine, with a
            # separate namespace; this does not imply channel-fence testing.
            snapshot=json.dumps(h.state(),sort_keys=True)
            token=qr_ai._message_identity.set("other-conversation-"+str(uuid4()))
            try:
                other=await qr_ai.turn(h.tenant,"5567999999999","Quanto custa barba?",h.redis,
                    h.namespace+":other",preview=False)
                assert "20" in other["answer"]
            finally:
                qr_ai._message_identity.reset(token)
            assert json.dumps(h.state(),sort_keys=True)==snapshot
    confirmations=[effect for effect in h.calls["writes"] if effect[0]=="confirm"]
    if journey.startswith(("JRN03","JRN07","JRN08")):
        assert not confirmations
    else:
        assert len(confirmations)==1 and result["step"]=="confirmed", (journey,result,h.calls["writes"])
        assert not h.state().get("hold") and not h.state().get("confirmation_message_id")
        assert h.state().get("history"), "Ending a task must retain bounded dialogue, never consent"
    assert not any("pai do" in name.casefold() for name in h.calls["names"])
