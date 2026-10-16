"""Google Calendar: cliente oficial via OAuth 2.0, com Fake para testes."""
import os
import httpx
from datetime import datetime
from uuid import UUID


class FakeCalendar:
    def __init__(self):
        self.events: dict[tuple[str,str], dict] = {}
        self.fail = False

    async def apply(self, calendar_id: str, event_id: str, action: str, appointment: dict):
        if self.fail:
            raise RuntimeError("falha simulada da API")
        key=(calendar_id,event_id)
        if action=="delete":
            self.events.pop(key,None)
        else:
            self.events[key]=appointment
        return event_id


class GoogleCalendar:
    """O calendário nunca determina a disponibilidade: Postgres é autoridade."""

    async def refresh_access_token(self, refresh_token: str) -> str:
        cid,secret=os.getenv("GOOGLE_CLIENT_ID"),os.getenv("GOOGLE_CLIENT_SECRET")
        if not cid or not secret:
            raise RuntimeError("OAuth Google não configurado")
        async with httpx.AsyncClient(timeout=15) as client:
            r=await client.post("https://oauth2.googleapis.com/token",data={
                "client_id":cid, "client_secret":secret, "refresh_token":refresh_token,
                "grant_type":"refresh_token",
            })
            r.raise_for_status()
            return r.json()["access_token"]

    async def apply(self, access_token: str, calendar_id: str, event_id: str,
                    action: str, appointment: dict):
        from urllib.parse import quote
        base="https://www.googleapis.com/calendar/v3/calendars/"+quote(calendar_id,safe="")+"/events"
        event_url=base+"/"+quote(event_id,safe="")
        headers={"Authorization":"Bearer "+access_token}
        start=appointment["start_at"].isoformat()
        end=appointment["end_at"].isoformat()
        event={"id":event_id, "summary":"Agendamento "+appointment["protocol"],
               "start":{"dateTime":start},"end":{"dateTime":end},
               "extendedProperties":{"private":{"appointment_id":str(appointment["id"])}}}
        async with httpx.AsyncClient(timeout=15) as client:
            if action=="delete":
                r=await client.delete(event_url,headers=headers)
                if r.status_code==404:return event_id
            elif action=="create":
                r=await client.post(base,headers=headers,json=event)
                # Colisão idempotente com event_id determinístico (já criado).
                if r.status_code==409:return event_id
            else:
                r=await client.put(event_url,headers=headers,json=event)
                # Se o evento foi apagado, o worker deve tentar criar de novo.
                if r.status_code==404:
                    r=await client.post(base,headers=headers,json=event)
            r.raise_for_status()
        return event_id


def deterministic_event_id(appointment_id: UUID) -> str:
    # Alphabet usado pelo Google Calendar: 0-9, a-v. UUID hex é subconjunto.
    return "orion"+appointment_id.hex
