"""Explicit names only; no AI, database or production calls."""
import pytest

from app.qr_customer import supplied_name


@pytest.mark.parametrize(("message", "expected"), [
    ("Sou o Carlos", "Carlos"),
    ("Sou a Maria e gostaria de marcar", "Maria"),
    ("Eu sou o Carlos e queria cortar o cabelo", "Carlos"),
    ("Meu nome é João e queria marcar", "João"),
    ("Meu nome e João e quero cortar o cabelo", "João"),
    ("Me chamo Maria de Souza!", "Maria de Souza"),
    ("Pode me chamar de Luís Antônio, por favor", "Luís Antônio"),
    ("Meu nome é Ana-Maria D'Ávila", "Ana-Maria D'Ávila"),
    ("Me chamo Ana e Maria", "Ana e Maria"),
    ("Oi sou o Carlos", "Carlos"),
    ("Olá, sou o Carlos", "Carlos"),
    ("Não, meu nome é João", "João"),
    ("Me chamo   Maria   de   Souza e desejo fazer a barba", "Maria de Souza"),
    ("Sou o Carlos que falou ontem", "Carlos"),
    ("Meu nome é João preciso de um corte", "João"),
    ("Meu nome é João. Amanhã queria marcar", "João"),
    ("Me chamo José A. Silva", "José A. Silva"),
])
def test_explicit_declaration_keeps_only_the_name(message, expected):
    assert supplied_name(message) == expected


@pytest.mark.parametrize("message", [
    "Nossa, João é legal", "Quero cortar com Carlos", "O Bruno",
    "Não me chamo João", "Meu nome não é João", "Quem se chama João?",
    "Você pode dizer meu nome é João?", "Se eu disser me chamo João, o que acontece?",
    'Ele disse: "me chamo João"', "'Meu nome é João'",
    "Sou o cliente de ontem", "Sou a responsável pela loja", "Sou o barbeiro",
    "Meu nome é A", "Me chamo " + "A" * 61, "Sou o " + "A" * 200,
])
def test_messages_without_a_safe_declaration_do_not_invent_a_name(message):
    assert supplied_name(message) is None
