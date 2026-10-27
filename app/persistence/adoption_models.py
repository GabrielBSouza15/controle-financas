"""Aceite local da base principal, sem fabricar interpretações do histórico."""
from sqlalchemy import Integer,String,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base
class HistoryAdoption(Base):
    __tablename__="history_adoption"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    source_sha256:Mapped[str]=mapped_column(String(64))
    source_fingerprint:Mapped[str]=mapped_column(String(64))
    recorded_at:Mapped[str]=mapped_column(String(32))
    acceptance_json:Mapped[str]=mapped_column(String())
    __table_args__=(CheckConstraint("id=1 AND length(source_sha256)=64 AND length(source_fingerprint)=64",name="identity"),
        CheckConstraint("json_valid(acceptance_json)",name="acceptance"))
