"""Coincidência exige conferência; sem inferir identidade ou apagar origem."""
import json
from datetime import datetime, timezone
from sqlalchemy import select
from .models import Entry, Obligation, Invoice, Responsible, Card, HistoryBatch, HistoryRecord, HistoryEntryReview
from .entries import PersistenceValidationError, IdempotencyConflict

class HistoryOverlapConflict(PersistenceValidationError):
    def __init__(self, matches):
        super().__init__("Há parcelas semelhantes no histórico. Confira as origens; só confirme se este for um compromisso diferente.")
        self.matches = matches

def normalized(value):
    return " ".join((value or "").split()).casefold()

def replay_review(session, entry_id, confirmation):
    review = session.scalar(select(HistoryEntryReview).where(HistoryEntryReview.entry_id == entry_id))
    expected = {"record_ids": json.loads(review.matches_json), "reason": review.reason} if review else None
    if confirmation != expected:
        raise IdempotencyConflict("A chave já foi usada com outra conferência histórica.")

def check_history_overlap(session, entry, confirmation=None):
    # No cartão, comparar o ciclo da parcela, pois a data original não é a data de pagamento.
    people = session.get(Responsible, entry.responsible_id).code
    card = session.get(Card, entry.card_id) if entry.card_id else None
    codes = {"Nubank": "nubank", "Mercado Pago/Livre": "mercado_pago"}
    card_code = codes.get(card.name) if card else None
    parts = session.execute(select(Obligation, Invoice.reference_month)
        .outerjoin(Invoice).where(Obligation.entry_id == entry.id)).all()
    from app.services.history_state import records_state
    state=records_state(session)
    matches = []
    rows = session.scalars(select(HistoryRecord).join(HistoryBatch).where(
        HistoryBatch.posted.is_(True), HistoryRecord.role == "obligation",
        HistoryRecord.kind == entry.kind, HistoryRecord.responsible_code == people,
        HistoryRecord.payment_method.in_(("pix", "cash") if entry.payment_method != "credit" else ("credit",))))
    for row in rows:
        if row.card_code != card_code or normalized(row.description) != normalized(entry.description):
            continue
        if any(state[row.id]["effective_cents"] == part.amount_cents and
               (state[row.id]["current_cycle"] == cycle if entry.card_id else row.reference_on == part.scheduled_date)
               for part, cycle in parts):
            matches.append({"record_id": row.id, "source_row": row.source_row,
                            "description": row.description, "amount_cents": row.amount_cents,
                            "reference_on": row.reference_on.isoformat()})
    ids = sorted(m["record_id"] for m in matches)
    if not ids:
        if confirmation is not None:
            raise PersistenceValidationError("A conferência não corresponde ao lançamento atual. Revise os dados.")
        return
    if (not isinstance(confirmation, dict) or set(confirmation) != {"record_ids", "reason"}
            or confirmation["record_ids"] != ids):
        raise HistoryOverlapConflict(matches)
    reason = confirmation["reason"]
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise PersistenceValidationError("Justifique por que se trata de outro compromisso.")
    session.add(HistoryEntryReview(entry_id=entry.id, reason=reason,
        matches_json=json.dumps(ids), recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds")))
    session.flush()
