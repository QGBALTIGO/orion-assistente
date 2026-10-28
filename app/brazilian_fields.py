"""Canonical Brazilian form values, independent of browser presentation.

Document checks validate syntax/check digits, not registration or ownership.
CNPJ weights and ASCII values follow Receita Federal/SERPRO's technical manual:
https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj/manual-dv-cnpj.pdf
"""
import re


BRAZILIAN_STATES = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
})


def reject_boolean_money(value):
    # Older catalog endpoints accept integral JSON numbers and integer strings.
    # Preserve those contracts, but a checkbox must never turn into one cent.
    if isinstance(value, bool):
        raise ValueError("Informe um valor monetário em centavos, não verdadeiro ou falso")
    return value


def normalize_state(value: str | None) -> str | None:
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if not isinstance(value, str) or value.strip().upper() not in BRAZILIAN_STATES:
        raise ValueError("Selecione uma UF brasileira válida")
    return value.strip().upper()


def normalize_postal_code(value: str | None) -> str | None:
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{5}-?[0-9]{3}", value.strip()):
        raise ValueError("Informe um CEP com oito dígitos")
    digits = value.strip().replace("-", "")
    return digits[:5] + "-" + digits[5:]


def _mod11_digit(total: int) -> str:
    remainder = total % 11
    return str(0 if remainder < 2 else 11 - remainder)


def valid_cpf(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{11}", value) or len(set(value)) == 1:
        return False
    first = _mod11_digit(sum(int(char) * weight for char, weight in zip(value[:9], range(10, 1, -1))))
    second = _mod11_digit(sum(int(char) * weight for char, weight in zip(value[:9] + first, range(11, 1, -1))))
    return value[-2:] == first + second


def valid_cnpj(value: str) -> bool:
    if not re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", value) or value == "0" * 14:
        return False
    # A=65−48=17, through Z=90−48=42; the numeric algorithm is unchanged.
    def digit(base: str) -> str:
        return _mod11_digit(sum((ord(char) - 48) * (2 + index % 8)
                               for index, char in enumerate(reversed(base))))
    first = digit(value[:12])
    return value[-2:] == first + digit(value[:12] + first)


def normalize_tax_document(kind: str, value: str | None, *, check_digits: bool = True) -> str | None:
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if kind not in {"cpf", "cnpj"}:
        raise ValueError("Selecione o tipo do documento")
    if not isinstance(value, str):
        raise ValueError("Informe o documento como texto, preservando os zeros iniciais")
    raw = value.strip()
    allowed = r"[0-9./ -]+" if kind == "cpf" else r"[A-Za-z0-9./ -]+"
    if not re.fullmatch(allowed, raw):
        raise ValueError("O documento contém caracteres inválidos")
    canonical = re.sub(r"[./ -]", "", raw).upper()
    pattern = r"[0-9]{11}" if kind == "cpf" else r"[A-Z0-9]{12}[0-9]{2}"
    if not re.fullmatch(pattern, canonical):
        raise ValueError("O documento não corresponde ao tipo selecionado")
    if check_digits and not (valid_cpf(canonical) if kind == "cpf" else valid_cnpj(canonical)):
        raise ValueError(f"{kind.upper()} inválido. Confira os dígitos informados")
    return canonical
