"""Orçamento registrado + estimativas mensais, com caixa em perspectiva separada."""
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.domain.dates import add_months
from app.domain.cards import CardRule, first_invoice_cycle
from app.persistence.models import Entry, Obligation, Invoice, Responsible, RecurrenceSeries, CardRuleVersion, FinancialEvent, Account
from app.persistence.queries import active_entry_filter
from app.persistence.settlements import effective_amounts, active_event_filter
from .recurrences import occurrence_at
from .settlements import safe, payer_code
from .adjustments import cash_extras
from app.domain.settlements import cash_direction

def budget_order(row):
    return (row["month"], row.get("scheduled_on") or row["month"], row.get("source", "new"),
            row.get("phase", "registered"),
            row.get("history_record_id") or row.get("obligation_id") or row.get("series_id") or row.get("invoice_id") or 0,
            row.get("description") or "")


def projection_in_session(session,month_from,months,*,card_id=None,responsible_id=None,limit=100,offset=0):
    refs=[add_months(month_from,i) for i in range(months)]
    result={ref:{"month":ref.isoformat(),"registered_expense_cents":0,"registered_self_expense_cents":0,
        "registered_self_income_cents":0,"forecast_expense_cents":0,"forecast_self_expense_cents":0,
        "forecast_self_income_cents":0,"parents_expense_cents":0,"cash_in_cents":0,"cash_out_cents":0} for ref in refs}
    items=[]
    from .parent_budget import choices, budget_month
    repayment_choices=choices(session)
    def include(month,kind,person,amount,phase,**extra):
        if month not in result:return
        if card_id is not None and extra.get("card_id")!=card_id:return
        if responsible_id is not None and extra.get("responsible_id")!=responsible_id:return
        row=result[month]
        if kind=="expense":
            row[f"{phase}_expense_cents"]+=amount
            if person=="self":row[f"{phase}_self_expense_cents"]+=amount
            else:row["parents_expense_cents"]+=amount
        elif person=="self":row[f"{phase}_self_income_cents"]+=amount
        items.append({"month":month.isoformat(),"kind":kind,"responsible_code":person,"amount_cents":safe(amount),
            "phase":phase,**extra})
    parts=session.execute(select(Obligation,Entry,Responsible.code,Invoice.reference_month)
        .join(Entry,Entry.id==Obligation.entry_id).join(Responsible,Responsible.id==Entry.responsible_id)
        .outerjoin(Invoice,Invoice.id==Obligation.invoice_id).where(active_entry_filter())).all()
    deltas=effective_amounts(session,[part.id for part,_,_,_ in parts])
    for part,entry,code,cycle in parts:
        include(cycle or budget_month(part.scheduled_date,repayment_choices.get(entry.id)),entry.kind,code,part.amount_cents+deltas[part.id],"registered",
            parents_repayment=repayment_choices.get(entry.id),
            entry_id=entry.id,obligation_id=part.id,description=entry.description,number=part.number,
            card_id=entry.card_id,responsible_id=entry.responsible_id,scheduled_on=part.scheduled_date.isoformat(),
            category_id=entry.category_id,payment_method=entry.payment_method,installment_count=entry.installment_count)
    # Histórico observado tem origem própria: não criar Entry nem completar contratos.
    from .history_budget import budget_rows
    for historic in budget_rows(session):
        item = dict(historic)
        include(item.pop("month"), item.pop("kind"), item.pop("responsible_code"), item.pop("amount_cents"), "registered", **item)
    # Uma compra no mês anterior pode entrar no primeiro ciclo solicitado.
    start=date(month_from.year-1,12,1) if month_from.month==1 and month_from.year>1 else date(month_from.year,month_from.month-1,1) if month_from.month>1 else month_from
    period_count=months+(start!=month_from)
    for series in session.scalars(select(RecurrenceSeries)):
        person=session.get(Responsible,series.responsible_id).code
        for i in range(period_count):
            month=add_months(start,i);occurrence=occurrence_at(session,series,month)
            if occurrence["status"]!="forecast":continue
            scheduled=date.fromisoformat(occurrence["scheduled_on"]);budget_month=month;missing_rule=False
            if series.card_id is not None:
                rule=session.scalar(select(CardRuleVersion).where(CardRuleVersion.card_id==series.card_id,
                    CardRuleVersion.effective_from<=scheduled).order_by(CardRuleVersion.effective_from.desc()))
                if rule is None:missing_rule=True
                else:
                    configured=CardRule("Cartão",rule.closing_day,rule.due_day,rule.include_closing_day,rule.due_month_offset)
                    budget_month=first_invoice_cycle(scheduled,configured).closing_date.replace(day=1)
            include(budget_month,series.kind,person,occurrence["amount_cents"],"forecast",
                series_id=series.id,reference_month=month.isoformat(),description=series.description,number=1,
                card_id=series.card_id,responsible_id=series.responsible_id,scheduled_on=scheduled.isoformat(),missing_rule=missing_rule,
                category_id=series.category_id,payment_method=series.payment_method,installment_count=1)
    # A mesma composição de caixa alimenta projeções e dashboard.
    for movement in cash_records(session):
        ref=date.fromisoformat(movement["occurred_on"]).replace(day=1)
        if ref in result:result[ref][f'cash_{movement["direction"]}_cents']+=movement["amount_cents"]
    for row in result.values():
        row["total_self_income_cents"]=safe(row["registered_self_income_cents"]+row["forecast_self_income_cents"])
        row["total_self_expense_cents"]=safe(row["registered_self_expense_cents"]+row["forecast_self_expense_cents"])
        row["all_expense_cents"]=safe(row["registered_expense_cents"]+row["forecast_expense_cents"])
        row["projected_self_result_cents"]=row["registered_self_income_cents"]+row["forecast_self_income_cents"]-row["registered_self_expense_cents"]-row["forecast_self_expense_cents"]
        row["cash_variation_cents"]=row["cash_in_cents"]-row["cash_out_cents"]
        for key,value in row.items():
            if key.endswith("_cents"):safe(value)
    items.sort(key=budget_order)
    return {"months":list(result.values()),"items":items[offset:] if limit is None else items[offset:offset+limit],"total":len(items),"limit":limit,"offset":offset,
        "cash_scope":"all_accounts_unfiltered","cash_is_partial":True,"includes_bank_opening_balance":False}


def cash_records(session):
    """Cada evento efetivo e cada ponta de transferência, sem pagamento externo fictício."""
    names=dict(session.execute(select(Account.id,Account.name)).all())
    items=[]
    for event in session.scalars(select(FinancialEvent).where(active_event_filter())):
        direction=cash_direction(event.kind,payer_code(session,event.payer_id))
        if direction!="none":
            items.append({"id":event.id,"kind":event.kind,"occurred_on":event.occurred_on.isoformat(),
                "amount_cents":safe(event.amount_cents),"direction":direction,
                "account_id":event.account_id,"reason":event.reason})
    items.extend(cash_extras(session))
    for item in items:
        item["account_name"]=names.get(item["account_id"],"Conta não informada")
    return items


def projection(engine,month_from,months,*,card_id=None,responsible_id=None,limit=100,offset=0):
    with Session(engine) as session:
        return projection_in_session(session,month_from,months,card_id=card_id,
            responsible_id=responsible_id,limit=limit,offset=offset)
