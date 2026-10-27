"""Consulta unificada de faturas, sem materializar histórico como compra nova."""
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.persistence.models import Card, Invoice, Responsible, Obligation, Entry
from app.persistence.queries import active_entry_filter
from app.persistence.entries import PersistenceValidationError
from .invoices import invoice_summaries
from .history_budget import budget_rows
from .settlements import safe, today_local
from .projections import projection_in_session


def forecast_period(month_from, month_to):
    # Sem mês final, consultar até doze meses; nunca projetar uma regra infinita.
    start = month_from or (month_to or today_local().replace(day=1))
    end = month_to
    if end is None:
        from app.domain.dates import add_months
        end = add_months(start, min(11, (9999-start.year)*12+12-start.month))
    months = (end.year-start.year)*12+end.month-start.month+1
    if not 1 <= months <= 120:
        raise PersistenceValidationError("Para incluir estimativas, escolha um período de até 120 meses.")
    return start, end, months


def list_overview(engine, *, card_id=None, responsible_id=None, month_from=None, month_to=None,
                  status=None, limit=50, offset=0, include_forecasts=False):
    if month_from and month_to and month_from > month_to:
        raise PersistenceValidationError("O mês inicial deve ser anterior ou igual ao final.")
    with Session(engine) as session:
        cards = {c.id:c.name for c in session.scalars(select(Card))}
        people = {p.id:p.code for p in session.scalars(select(Responsible))}
        rows = {}
        def group(cid, month):
            return rows.setdefault((cid,month), {
                "card_id":cid,"card_name":cards[cid],"reference_month":month,
                "self_cents":0,"parents_cents":0,"statement_total_cents":0,
                "historical_count":0,"operational_count":0,"invoice_id":None,
                "operational_status":None,"responsible_codes":set(),
                "forecast_cents":0,"forecast_self_cents":0,"forecast_parents_cents":0,"forecast_count":0,
            })
        invoices = session.execute(select(Invoice,Card.name).join(Card)).all()
        present = {}
        for iid,code in session.execute(select(Obligation.invoice_id,Responsible.code)
                .join(Entry,Entry.id==Obligation.entry_id).join(Responsible,Responsible.id==Entry.responsible_id)
                .where(Obligation.invoice_id.is_not(None),active_entry_filter())):
            present.setdefault(iid,set()).add(code)
        for inv in invoice_summaries(session,invoices).values():
            r=group(inv["card_id"],inv["reference_month"])
            for name in ("self_cents","parents_cents","statement_total_cents"):r[name]+=inv[name]
            r.update(invoice_id=inv["id"],operational_status=inv["status"],
                     operational_count=inv["active_obligation_count"])
            r["responsible_codes"].update(present.get(inv["id"],set()))
        for item in budget_rows(session):
            if item["kind"]!="expense" or item["card_id"] is None:continue
            r=group(item["card_id"],item["month"].isoformat())
            r[item["responsible_code"]+"_cents"]+=item["amount_cents"]
            r["statement_total_cents"]+=item["amount_cents"]
            r["historical_count"]+=1;r["responsible_codes"].add(item["responsible_code"])
        period = forecast_period(month_from, month_to) if include_forecasts else None
        if period:
            for item in projection_in_session(session, period[0], period[2], card_id=card_id, limit=None)["items"]:
                if item["phase"] != "forecast" or item["kind"] != "expense" or item.get("card_id") is None:
                    continue
                r = group(item["card_id"], item["month"])
                r["forecast_cents"] += item["amount_cents"]
                r["forecast_"+item["responsible_code"]+"_cents"] += item["amount_cents"]
                r["forecast_count"] += 1
                r["responsible_codes"].add(item["responsible_code"])
        result=[]
        for r in rows.values():
            r["status"]="historical" if r["historical_count"] else r["operational_status"]
            r["source"]="mixed" if r["historical_count"] and r["invoice_id"] else "history" if r["historical_count"] else "new"
            r["registered_count"] = r["historical_count"] + r["operational_count"]
            if not r["registered_count"] and r["forecast_count"]:
                r["source"] = r["status"] = "forecast"
            r["projected_total_cents"] = r["statement_total_cents"] + r["forecast_cents"]
            for person in ("self", "parents"):
                r["projected_"+person+"_cents"] = r[person+"_cents"] + r["forecast_"+person+"_cents"]
            if card_id is not None and r["card_id"]!=card_id:continue
            if responsible_id is not None and people.get(responsible_id) not in r["responsible_codes"]:continue
            if month_from and r["reference_month"]<month_from.isoformat():continue
            if month_to and r["reference_month"]>month_to.isoformat():continue
            if status in {"open","closed"} and r["operational_status"]!=status:continue
            if status=="historical" and not r["historical_count"]:continue
            from .personal import bill_position
            r["payment_remaining_cents"]=bill_position(session,r["card_id"],date.fromisoformat(r["reference_month"]))["remaining_cents"]
            r.pop("responsible_codes")
            for key,value in r.items():
                if key.endswith("_cents"):safe(value)
            result.append(r)
        result.sort(key=lambda r:(r["reference_month"],r["card_name"]))
        return {"total":len(result),"items":result[offset:offset+limit],"limit":limit,"offset":offset,
                "forecast_month_from":period[0].isoformat() if period else None,
                "forecast_month_to":period[1].isoformat() if period else None}


def details(engine, month, card_id, *, limit=50, offset=0, include_forecasts=False):
    with Session(engine) as session:
        from .dashboard import budget_items
        from .dashboard_outlook import card_adjustments
        if session.get(Card,card_id) is None:
            raise PersistenceValidationError("Cartão não encontrado.")
        data=projection_in_session(session,month,1,card_id=card_id,limit=None)
        items=[dict(i,contribution_cents=i["amount_cents"]) for i in budget_items(session,data)
               if i["kind"]=="expense" and (include_forecasts or i["phase"]=="registered")]
        items.extend(i for i in card_adjustments(session,[month]) if i["card_id"]==card_id)
        invoice=session.scalar(select(Invoice).where(Invoice.card_id==card_id,Invoice.reference_month==month))
        from .projections import budget_order
        items.sort(key=budget_order)
        return {"card_id":card_id,"month":month.isoformat(),"invoice_id":invoice.id if invoice else None,
                "forecast_cents":safe(sum(i["contribution_cents"] for i in items if i.get("phase")=="forecast")),
                "registered_cents":safe(sum(i["contribution_cents"] for i in items if i.get("phase")!="forecast")),
                "total_cents":safe(sum(i["contribution_cents"] for i in items)),
                "self_cents":safe(sum(i["amount_cents"] for i in items if i.get("responsible_code")=="self")),
                "parents_cents":safe(sum(i["amount_cents"] for i in items if i.get("responsible_code")=="parents")),
                "items":items[offset:offset+limit],"total":len(items),"limit":limit,"offset":offset}
