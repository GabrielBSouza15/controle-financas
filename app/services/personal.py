"""Controles pessoais e quitação integral com os motores financeiros existentes."""
from datetime import date
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.database import transaction
from app.persistence.models import (PersonalOperation, Responsible, Invoice, Card, Obligation, Entry, HistoryRecord)
from app.persistence.queries import active_entry_filter
from app.persistence.settlements import allocation_totals, effective_amounts
from app.persistence.entries import PersistenceValidationError
from .settlements import safe, canonical, unused, replay, now_text, today_local, record_event_in_session
from .catalogs import ConflictError, NotFoundError

INITIAL_REINVESTMENT = 0

def reinvestment(session):
    row = session.scalar(select(PersonalOperation).where(PersonalOperation.kind == "reinvestment").order_by(PersonalOperation.id.desc()).limit(1))
    return json.loads(row.result_json) if row else {"amount_cents": INITIAL_REINVESTMENT, "version": 0}

def save_operation(session, data, kind, result):
    row = PersonalOperation(request_key=data["request_key"], kind=kind, payload_json=canonical(data),
                            result_json=canonical(result), recorded_at=now_text())
    session.add(row)
    session.flush()

def previous(session, data):
    row = session.scalar(select(PersonalOperation).where(PersonalOperation.request_key == data["request_key"]))
    if row:
        replay(row, canonical(data))
        return json.loads(row.result_json)
    unused(session, data["request_key"])

def update_reinvestment(engine, data):
    with transaction(engine) as session:
        old = previous(session, data)
        if old is not None: return old
        current = reinvestment(session)
        if current["version"] != data["version"]:
            raise ConflictError("O indicador mudou. Atualize antes de fazer outra alteração.")
        if data["mode"] not in {"add", "subtract"}:
            raise PersistenceValidationError("Escolha adicionar ou subtrair do total.")
        amount = safe(current["amount_cents"] + (data["amount_cents"] if data["mode"] == "add" else -data["amount_cents"]))
        if amount < 0:
            raise PersistenceValidationError("O valor a subtrair não pode exceder o total a ser reinvestido.")
        result = {"amount_cents": amount, "version": current["version"] + 1}
        save_operation(session, data, "reinvestment", result)
        return result

def bill_position(session, card_id, month):
    from .invoices import invoice_summaries
    from .history_budget import cycles
    invoice = session.get(Card, card_id)
    if invoice is None: raise NotFoundError("Cartão não encontrado.")
    invoices = session.execute(select(Invoice, Card.name).join(Card).where(Invoice.card_id == card_id, Invoice.reference_month == month)).all()
    operational = list(invoice_summaries(session, invoices).values())
    historical = [c for c in cycles(session, month) if c["card_name"] == invoice.name]
    return {"invoice_id": operational[0]["id"] if operational else None,
            "remaining_cents": safe(sum(max(0, i["issuer_remaining_cents"]) for i in operational + historical)),
            "operational": operational, "historical": historical}

def pay_bill(engine, data):
    from .history_events import record_event_in_session as historical_event, totals, position
    from .history_candidate import history_rows
    with transaction(engine) as session:
        old = previous(session, data)
        if old is not None: return old
        no_account = data.get("paid_without_account", False)
        if no_account == (data.get("account_id") is not None):
            raise PersistenceValidationError("Escolha pagamento sem conta ou informe uma conta, sem combinar os dois.")
        if data["occurred_on"] > today_local():
            raise PersistenceValidationError("A data efetiva não pode estar no futuro.")
        current = bill_position(session, data["card_id"], data["month"])
        if current["remaining_cents"] != data["expected_remaining_cents"]:
            raise ConflictError("O restante da fatura mudou. Atualize e confira antes de pagar.")
        if current["remaining_cents"] <= 0: raise ConflictError("Esta fatura não tem valor pendente.")
        person = session.scalar(select(Responsible).where(Responsible.code == "self"))
        common = dict(kind="invoice_payment", occurred_on=data["occurred_on"], account_id=data["account_id"], reason="Pagamento integral da fatura do mês")
        marks = []
        sequence = 0
        def send(rows, historical=False, invoice_id=None):
            nonlocal sequence
            if no_account:
                from app.persistence.models import BillPaymentMark
                for row in rows:
                    if not historical:
                        part = session.get(Obligation, row["obligation_id"])
                        if data["occurred_on"] < session.get(Entry, part.entry_id).occurred_on:
                            raise PersistenceValidationError("A data efetiva não pode preceder o lançamento de origem.")
                    marks.append(BillPaymentMark(operation_key=data["request_key"], occurred_on=data["occurred_on"], **row))
                return
            for start in range(0, len(rows), 100):
                chunk = rows[start:start+100]; sequence += 1
                payload = dict(common, request_key=data["request_key"] + ":" + str(sequence),
                               amount_cents=safe(sum(r["amount_cents"] for r in chunk)), allocations=chunk)
                if historical: historical_event(session, dict(payload, payer_code="self"))
                else: record_event_in_session(session, dict(payload, payer_id=person.id, invoice_id=invoice_id))
        for inv in current["operational"]:
            parts = session.scalars(select(Obligation).join(Entry).where(Obligation.invoice_id == inv["id"], active_entry_filter()).order_by(Obligation.id)).all()
            paid = allocation_totals(session, [p.id for p in parts]); deltas = effective_amounts(session, [p.id for p in parts])
            remaining = max(0, inv["issuer_remaining_cents"]); rows=[]
            if inv["issuer_unallocated_cents"]: raise ConflictError("Há pagamento sem vínculo. Confira os vínculos antes de quitar a fatura.")
            for part in parts:
                value = min(remaining, max(0, part.amount_cents + deltas[part.id] - paid[part.id]["payment_cents"] - paid[part.id]["credit_applied_cents"]))
                if value: rows.append(dict(obligation_id=part.id, amount_cents=value)); remaining -= value
            if remaining: raise ConflictError("Confira os créditos da fatura antes de pagar.")
            send(rows, invoice_id=inv["id"])
        usage=totals(session)
        for cycle in current["historical"]:
            remaining=max(0, cycle["issuer_remaining_cents"]); rows=[]
            for row in history_rows(session):
                if row.card_code == cycle["card_code"] and usage[row.id]["current_cycle"] == data["month"] and row.role == "obligation":
                    value=min(remaining, position(row, usage)["pending_cents"])
                    if value: rows.append(dict(record_id=row.id, amount_cents=value)); remaining-=value
            if remaining or sum(r["amount_cents"] for r in rows) != max(0,cycle["pending_cents"]):
                raise ConflictError("Há crédito ou pagamento sem vínculo no histórico. Confira antes de quitar.")
            send(rows, historical=True)
        result={"paid_cents": current["remaining_cents"]}
        save_operation(session, data, "bill_payment", result)
        session.add_all(marks)
        session.flush()
        return result

def flow(engine, date_from, date_to, limit=50, offset=0, *, q=""):
    from .projections import projection_in_session
    from .dashboard import budget_items
    from .history_events import totals, position
    from .history_candidate import history_rows
    from .legacy_reader import normalized
    if date_to < date_from or (date_to-date_from).days > 3660:
        raise PersistenceValidationError("Escolha um período válido de até dez anos.")
    count=(date_to.year-date_from.year)*12+date_to.month-date_from.month+1
    search = normalized(q)
    start, end = date_from.isoformat(), date_to.isoformat()
    with Session(engine) as session:
        data=projection_in_session(session,date_from.replace(day=1),count,limit=None,all_registered_items=True)
        items=budget_items(session,data)
        paid=allocation_totals(session,[i["obligation_id"] for i in items if i.get("obligation_id")],include_payment_dates=True)
        usage=totals(session,include_payment_dates=True); historic={r.id:r for r in history_rows(session)}
        from app.persistence.models import BillPaymentMark
        marks = list(session.scalars(select(BillPaymentMark)))
        marked_parts = {m.obligation_id:m for m in marks if m.obligation_id}
        marked_history = {m.record_id:m for m in marks if m.record_id}
        result=[]
        for item in items:
            if search and search not in normalized(item["description"]):
                continue
            is_card = item.get("card_id") is not None
            day = item["month"] if is_card else item["scheduled_on"]
            if is_card and not start[:7] <= day[:7] <= end[:7]:
                continue
            pending=item["amount_cents"]; status="Previsto"
            payments = {}
            undated = 0
            if item["phase"] == "registered":
                if item.get("history_record_id"):
                    pos=position(historic[item["history_record_id"]],usage); pending=pos["pending_cents"]
                    status="Pago sem data (planilha)" if pos["payment_state"]=="paid_undated" else "Pago" if pending==0 else "Parcial" if pending<item["amount_cents"] else "Pendente"
                    payments = pos.get("payments_by_date", {})
                    undated = pos["baseline_cents"]
                else:
                    p=paid[item["obligation_id"]]; pending=max(0,item["amount_cents"]-p["payment_cents"]-p["credit_applied_cents"])
                    status="Pago" if pending==0 else "Parcial" if pending<item["amount_cents"] else "Pendente"
                    payments = p.get("payments_by_date", {})
            if not is_card and payments:
                # A linha do compromisso é distribuída entre quitações e restante.
                # Ajustes podem reduzir o valor exibido, sem reescrever o pagamento.
                applicable = max(0, item["amount_cents"] - pending - undated)
                remaining = item["amount_cents"]
                for payment_date, amount in sorted(payments.items()):
                    amount = min(amount, applicable)
                    applicable -= amount
                    remaining -= amount
                    effective_day = payment_date.isoformat()
                    if amount and start <= effective_day <= end:
                        result.append(dict(item, date=effective_day, effective_on=effective_day,
                                           amount_cents=safe(amount), pending_cents=0, payment_status="Pago"))
                if not remaining:
                    continue
                item = dict(item, amount_cents=safe(remaining))
                if remaining == pending:
                    status = "Pendente"
                elif not pending and undated:
                    status = "Pago sem data (planilha)"
            if not is_card and not start <= day <= end:
                continue
            mark = marked_history.get(item.get("history_record_id")) if item.get("history_record_id") else marked_parts.get(item.get("obligation_id"))
            if mark:
                status += " · sem conta em " + mark.occurred_on.strftime("%d/%m/%Y")
            result.append(dict(item,date=day,payment_status=status,pending_cents=safe(pending)))
        result.sort(key=lambda i:(i["date"],i["scheduled_on"],i.get("history_record_id") or i.get("obligation_id") or i.get("series_id") or 0), reverse=True)
        page = result[offset:offset + limit]
        entry_ids = {row["entry_id"] for row in page if row.get("entry_id") and row.get("card_id")}
        if entry_ids:
            purchase_dates = dict(session.execute(
                select(Entry.id, Entry.occurred_on).where(Entry.id.in_(entry_ids))
            ).all())
            for row in page:
                if row.get("card_id") and row.get("entry_id"):
                    row["purchase_on"] = purchase_dates[row["entry_id"]].isoformat()
        return dict(items=page, total=len(result), limit=limit, offset=offset)
