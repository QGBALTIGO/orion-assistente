"""Teste único autorizado da entrega do Orion ao próprio WhatsApp conectado.

Operação temporária de homologação; NÃO usar como predeploy recorrente.
Somente o número autenticado retornado pelo bridge poderá receber.
Nenhum número é incluído em log.
"""
import asyncio

from app.qr_api import _bridge


async def run() -> None:
    status = await _bridge("/internal/status")
    if status.get("status") != "connected":
        print("ORION_DELIVERY_TEST_SKIPPED:not_connected")
        return
    phone = status.get("phone")
    if not isinstance(phone, str) or not phone.isdigit() or not 8 <= len(phone) <= 15:
        print("ORION_DELIVERY_TEST_SKIPPED:unknown_owned_number")
        return
    data = await _bridge("/internal/send", "POST", {
        "phone": phone,
        "text": "🧪 Teste Orion: verificando entrega após atualização da criptografia. "
                "Esta mensagem foi enviada uma única vez para a própria conta conectada.",
    })
    if not data.get("accepted"):
        print("ORION_DELIVERY_TEST_NOT_ACCEPTED")
        return
    sent_id = data.get("id")
    print("ORION_DELIVERY_TEST_ACCEPTED_UNVERIFIED")
    for _ in range(7):
        await asyncio.sleep(3)
        snapshot = await _bridge("/internal/messages", query={"phone": phone})
        match = next((m for m in snapshot.get("items",[]) if m.get("id")==sent_id),None)
        if match and match.get("delivery") in ("delivered","read","played"):
            print("ORION_DELIVERY_TEST_CONFIRMED:"+match["delivery"])
            return
    print("ORION_DELIVERY_TEST_WAITING_FOR_RECEIPT")
    # Lack of an ACK is not proof of failure; physical handset must confirm.


if __name__=="__main__":
    asyncio.run(run())
