"""Bounded, factual quick replies. No transport, code execution or side effects."""
import re
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from app.catalog_schemas import StrictModel

VARIABLES = frozenset({
    "cliente.nome", "cliente.primeiro_nome", "empresa.nome", "empresa.endereco",
    "agendamento.data", "agendamento.horario", "agendamento.protocolo",
    "profissional.nome", "servico.nome", "servico.valor", "empresa.link_agendar",
})
TOKEN = re.compile(r"\{\{\s*([a-z_]+\.[a-z_]+)\s*\}\}")


def template_variables(value: str) -> set[str]:
    if (not isinstance(value, str) or not value.strip() or len(value) > 1200 or "\x00" in value
            or re.search(r"[\ud800-\udfff]", value)):
        raise ValueError("Use um texto de até 1200 caracteres")
    variables = set(TOKEN.findall(value))
    if variables - VARIABLES or "{" in TOKEN.sub("", value) or "}" in TOKEN.sub("", value):
        raise ValueError("Variável desconhecida ou incompleta")
    return variables


def render_reply(value: str, facts: dict) -> str:
    required = template_variables(value)
    missing = sorted(key for key in required if not isinstance(facts.get(key), str) or not facts[key].strip())
    if missing:
        raise ValueError("Dados reais indisponíveis: " + ", ".join(missing))
    result = TOKEN.sub(lambda match: facts[match[1]].strip(), value).strip()
    if len(result.encode("utf-16-le")) // 2 > 1200:
        raise ValueError("A resposta preenchida ultrapassa 1200 caracteres")
    return result


class LabelSave(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#18836b", pattern=r"^#[0-9a-fA-F]{6}$")
    expected_version: StrictInt = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Informe um nome legível")
        return value


class QuickReplySave(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="Geral", min_length=1, max_length=60)
    visibility: Literal["company", "personal"] = "company"
    text: str = Field(min_length=1, max_length=1200)
    expected_version: StrictInt = Field(default=0, ge=0)

    @field_validator("text")
    @classmethod
    def valid_template(cls, value):
        template_variables(value)
        return value.strip()

    @field_validator("title", "category")
    @classmethod
    def clean_label(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Preencha o nome")
        return value
