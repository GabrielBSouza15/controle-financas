"""Pendências de ação dos dois meses atuais; consultas não confirmam fatos."""
from sqlalchemy import select
from app.domain.dates import add_months
from app.persistence.models import Card, RecurrenceSeries, Responsible
from .recurrences import occurrence_at
from .personal import bill_position
from .settlements import today_local, safe


def pending_tasks(session):
    start = today_local().replace(day=1)
    months = [start, add_months(start, 1)]
    people = dict(session.execute(select(Responsible.id, Responsible.code)).all())
    cards = list(session.scalars(select(Card).order_by(Card.name, Card.id)))
    card_names = {c.id: c.name for c in cards}
    items = []
    for series in session.scalars(select(RecurrenceSeries)):
        for month in months:
            occurrence = occurrence_at(session, series, month)
            if occurrence["status"] != "forecast":
                continue
            items.append(dict(kind="recurrence", series_id=series.id,
                reference_month=month.isoformat(), scheduled_on=occurrence["scheduled_on"],
                description=series.description, responsible_code=people[series.responsible_id],
                entry_kind=series.kind, card_name=card_names.get(series.card_id),
                amount_cents=safe(occurrence["amount_cents"])))
    for month in months:
        for card in cards:
            position = bill_position(session, card.id, month)
            if position["remaining_cents"] > 0:
                items.append(dict(kind="invoice", card_id=card.id, card_name=card.name,
                    description=card.name, reference_month=month.isoformat(),
                    amount_cents=position["remaining_cents"]))
    items.sort(key=lambda r: (r["reference_month"], r.get("scheduled_on", r["reference_month"]),
                              r["kind"], r["description"], r.get("series_id", r.get("card_id"))))
    return dict(month_from=start.isoformat(), month_to=months[-1].isoformat(), items=items,
                count=len(items), scope="all_responsibles_current_and_next_month")
