"""Database exceptions may contain SQL parameters; never forward them to logs."""
from functools import wraps
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError


def sanitized_database_errors(fn):
    @wraps(fn)
    async def wrapped(*args,**kwargs):
        try:return await fn(*args,**kwargs)
        except SQLAlchemyError:
            raise HTTPException(503,'Não foi possível consultar o agendamento agora. Tente novamente em instantes.') from None
    return wrapped
