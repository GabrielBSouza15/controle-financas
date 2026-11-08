"""Relatório privado de despesas: somente leitura, valores inteiros do orçamento."""
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfgen.canvas import Canvas

from app.domain.dates import add_months
from app.persistence.models import Card
from .dashboard import details_in_session
from .settlements import safe

MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
PEOPLE = {"self": "Eu", "parents": "Pai/Mãe", "all": "Todos"}


def month_label(month):
    return f"{MONTHS[month.month - 1]} de {month.year}"


def money(cents):
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}R$ {whole:,}".replace(",", ".") + f",{fraction:02d}"


def report_data(engine, month, responsible_code, **filters):
    next_month = add_months(month, 1)
    with Session(engine) as session:
        # As duas composições compartilham a mesma transação de leitura.
        current = details_in_session(session, month, "expense", responsible_code=responsible_code, limit=None, **filters)
        following = details_in_session(session, next_month, "expense", responsible_code=responsible_code, limit=None, **filters)
        cards = list(session.execute(select(Card.id, Card.name).order_by(Card.id)))
    items = current["items"]
    totals = [("PIX/Débito", safe(sum(r["amount_cents"] for r in items if r.get("card_id") is None)))]
    totals.extend((name, safe(sum(r["amount_cents"] for r in items if r.get("card_id") == card_id))) for card_id, name in cards)
    return dict(month=month, responsible_code=responsible_code, items=items,
                total_cents=current["total_cents"], methods=totals,
                registered_cents=safe(sum(r["amount_cents"] for r in items if r["phase"] == "registered")),
                forecast_cents=safe(sum(r["amount_cents"] for r in items if r["phase"] == "forecast")),
                people=[(PEOPLE[code], safe(sum(r["amount_cents"] for r in items if r["responsible_code"] == code))) for code in ("self", "parents")],
                next_month=next_month, next_total_cents=following["total_cents"])


class ReportCanvas(Canvas):
    """Numeração completa após conhecer a quantidade de páginas."""
    def __init__(self, *args, generated_at, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_pages = []
        self.generated_at = generated_at

    def showPage(self):
        self.saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        count = len(self.saved_pages)
        for state in self.saved_pages:
            self.__dict__.update(state)
            self.setFillColor(colors.HexColor("#526171"))
            self.setFont("Helvetica", 8)
            self.drawString(36, 25, f"Gerado em {self.generated_at} (Brasília)")
            self.drawRightString(A4[0] - 36, 25, f"Página {self._pageNumber} de {count}")
            super().showPage()
        super().save()


def render_pdf(data, *, scope_label=None):
    output = BytesIO()
    width = A4[0] - 72
    ink, pale = map(colors.HexColor, ("#182433", "#EDF3F8"))
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=8, leading=11, textColor=ink)
    bold = ParagraphStyle("bold", parent=body, fontName="Helvetica-Bold")
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)
    title = ParagraphStyle("title", parent=bold, fontSize=21, leading=26)
    subtitle = ParagraphStyle("subtitle", parent=body, fontSize=11, leading=16)
    def para(value, style=body):
        return Paragraph(escape(str(value)), style)
    def table(rows, widths, header=False):
        result = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT", splitInRow=1)
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#D6DFE7"))]
        if header:
            commands.append(("BACKGROUND", (0, 0), (-1, 0), pale))
        result.setStyle(TableStyle(commands))
        return result
    story = [para("Despesas do mês", title), Spacer(1, 5),
             para(f"{month_label(data['month']).capitalize()} · {PEOPLE[data['responsible_code']]}", subtitle)]
    if scope_label:
        story.extend([Spacer(1, 4), para(scope_label)])
    story.extend([Spacer(1, 16), table([
        [para("Total geral", bold), para(money(data["total_cents"]), ParagraphStyle("total", parent=right, fontName="Helvetica-Bold", fontSize=15, leading=18))],
        *[[para(name), para(money(value), right)] for name, value in data["methods"]],
    ], [width * .68, width * .32]), Spacer(1, 10)])
    if any(r["phase"] == "forecast" for r in data["items"]):
        story.extend([para(f"Já lançados: {money(data['registered_cents'])} · Estimativas: {money(data['forecast_cents'])}"), Spacer(1, 8)])
    if data["responsible_code"] == "all":
        story.extend([para(" · ".join(f"{name}: {money(value)}" for name, value in data["people"])), Spacer(1, 8)])
    story.extend([para(f"{len(data['items'])} itens", bold), Spacer(1, 6)])
    all_people = data["responsible_code"] == "all"
    headings = ["Data", "Descrição", "Categoria", "Cartão / meio", "Parcela"] + (["Responsável"] if all_people else []) + ["Valor"]
    widths = [54, width - (397 if all_people else 329), 65, 82, 51] + ([68] if all_people else []) + [77]
    rows = [[para(h, ParagraphStyle("value_header", parent=bold, alignment=TA_RIGHT) if h == "Valor" else bold) for h in headings]]
    for item in data["items"]:
        description = item.get("description") or "Descrição ausente"
        if item["phase"] == "forecast":
            description += " (Estimativa)"
        values = [datetime.strptime(item["scheduled_on"], "%Y-%m-%d").strftime("%d/%m/%Y"), description,
                  item["category_name"], item.get("card_name") or "PIX/Débito", f"{item['number']}/{item['installment_count']}"]
        if all_people:
            values.append(PEOPLE[item["responsible_code"]])
        rows.append([para(v) for v in values] + [para(money(item["amount_cents"]), right)])
    if data["items"]:
        story.append(table(rows, widths, header=True))
    else:
        story.append(para("Nenhuma despesa neste mês para a seleção."))
    story.extend([Spacer(1, 16), table([[para(f"Total previsto para {month_label(data['next_month'])}", bold),
                 para(money(data["next_total_cents"]), ParagraphStyle("next", parent=right, fontName="Helvetica-Bold"))]], [width * .72, width * .28])])
    generated = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y às %H:%M")
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=34, bottomMargin=45,
                            title="Despesas do mês", author="Controle de finanças")
    doc.build(story, canvasmaker=lambda *args, **kw: ReportCanvas(*args, generated_at=generated, **kw))
    return output.getvalue()
