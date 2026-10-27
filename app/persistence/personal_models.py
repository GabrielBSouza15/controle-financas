"""Comandos pessoais independentes e recibos de pagamento integral."""
from sqlalchemy import Column, Integer, String
from .models import Base

class PersonalOperation(Base):
    __tablename__ = "personal_operations"
    id = Column(Integer, primary_key=True)
    request_key = Column(String(128), nullable=False, unique=True)
    kind = Column(String(32), nullable=False)
    payload_json = Column(String, nullable=False)
    result_json = Column(String, nullable=False)
    recorded_at = Column(String(32), nullable=False)
