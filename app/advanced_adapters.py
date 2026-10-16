"""Interfaces avançadas seguras, sem integrações externas habilitadas."""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class TranscriptionProvider(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> str: ...


class FakeTranscriber:
    """Somente testes sintéticos: NÃO é transcrição real."""
    async def transcribe(self, audio: bytes, mime: str) -> str:
        if not audio or len(audio) > 2_000_000:
            raise ValueError("Tamanho de áudio inválido")
        if mime not in ("audio/ogg", "audio/mpeg"):
            raise ValueError("Formato não autorizado")
        return "[transcrição fictícia para testes]"


@dataclass(frozen=True)
class ServerVerifiedScope:
    """Construir APENAS após autenticação ou webhook assinado."""
    tenant_id: UUID


class SafeFunctionGateway:
    """LLM não escolhe tenant nem reserva sem confirmação humana."""
    ALLOWED = frozenset({"availability", "help"})

    def __init__(self, verified_scope: ServerVerifiedScope):
        if not isinstance(verified_scope.tenant_id, UUID):
            raise ValueError("Escopo inválido")
        self.scope = verified_scope

    def validate(self, tool_name: str, arguments: dict):
        if tool_name not in self.ALLOWED:
            raise PermissionError("Ferramenta não autorizada")
        if "tenant_id" in arguments or "sql" in arguments or "token" in arguments:
            raise PermissionError("Modelo não determina segurança ou empresa")
        if len(arguments) > 8:
            raise ValueError("Argumentos excedem limite")
        return {"tenant_id": str(self.scope.tenant_id), "tool": tool_name}


class ChatwootAdapter:
    """Só conectar após aprovação e configuração do provedor."""
    async def notify_handoff(self, tenant: UUID, contact: str):
        raise RuntimeError("Chatwoot não conectado; usar inbox interna")
