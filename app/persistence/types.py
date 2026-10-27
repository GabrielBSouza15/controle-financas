"""Tipos de fronteira: dinheiro inteiro e data civil, sem coerção silenciosa."""

from datetime import date
from sqlalchemy import Date, Integer
from sqlalchemy.types import TypeDecorator

MAX_INTEGER = 2**63 - 1


class ExactInteger(TypeDecorator):
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and (type(value) is not int or not -2**63 <= value <= MAX_INTEGER):
            raise ValueError("Informe inteiro de 64 bits; float e bool não são aceitos.")
        return value


class CivilDate(TypeDecorator):
    impl = Date
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and type(value) is not date:
            raise ValueError("Informe date sem horário; datetime não é convertido implicitamente.")
        return value
