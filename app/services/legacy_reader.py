"""Leitura OOXML exata: preserva XML/caches, não executa Excel ou VBA."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from io import BytesIO
import ast
import json
import posixpath
import re
import unicodedata
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET
from app.domain.cards import NUBANK, MERCADO_PAGO, first_invoice_cycle

APPROVED_SOURCE_SHA256="a81fcf7382076b369974ace2ad8f3e3088b978cbfb8697188acb0220f3320e19"
POLICY_VERSION="observed-obligations-v1"
NS={"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
EXPECTED_HEADERS=["Data","Descrição","Parcela","Total de Parcelas","Categoria","Valor","Responsável","Tipo",
    "Meio de Pagamento","Banco","Pago","Total pago","Total a pagar","Total "]
MAX_BYTES=32*1024*1024

class LegacyValidationError(ValueError):
    pass

def normalized(value):
    if value is None:return ""
    text=" ".join(str(value).strip().split()).casefold()
    return "".join(c for c in unicodedata.normalize("NFKD",text) if not unicodedata.combining(c))

def document(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"))

def decimal_text(value):
    return format(value,"f") if value is not None else None

def xml_part(archive,name):
    try:
        info=archive.getinfo(name)
        if info.file_size>MAX_BYTES:raise LegacyValidationError("Parte XML excede o limite de leitura.")
        raw=archive.read(name)
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise LegacyValidationError("Declaração XML externa não é aceita.")
        return ET.fromstring(raw)
    except (KeyError,ET.ParseError) as exc:
        raise LegacyValidationError("Estrutura OOXML ausente ou inválida.") from exc

def target_path(base,target):
    if target.startswith("/"):path=target.lstrip("/")
    else:path=posixpath.normpath(posixpath.join(posixpath.dirname(base),target))
    if not path.startswith("xl/") or ".." in path.split("/"):
        raise LegacyValidationError("Relacionamento fora do pacote de planilha.")
    return path

def read_workbook(content):
    """Retorna todas as células e a tabela pelo nome, sem normalizar o original."""
    if len(content)>MAX_BYTES:raise LegacyValidationError("Arquivo excede o limite do importador.")
    try:archive=ZipFile(BytesIO(content))
    except BadZipFile as exc:raise LegacyValidationError("O arquivo não é um pacote XLSX/XLSM válido.") from exc
    with archive:
        if len(archive.infolist())>3000 or sum(x.file_size for x in archive.infolist())>128*1024*1024:
            raise LegacyValidationError("Pacote excede os limites de leitura.")
        workbook=xml_part(archive,"xl/workbook.xml")
        properties=workbook.find("s:workbookPr",NS)
        date1904=properties is not None and properties.get("date1904") in {"1","true"}
        strings=[]
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in xml_part(archive,"xl/sharedStrings.xml").findall("s:si",NS):
                strings.append("".join(node.text or "" for node in item.iter("{"+NS["s"]+"}t")))
        relations=xml_part(archive,"xl/_rels/workbook.xml.rels")
        targets={r.get("Id"):r.get("Target") for r in relations if r.get("TargetMode")!="External"}
        sheets={}; table_sheet=None; table_ref=None; headers=None
        for sheet in workbook.findall("s:sheets/s:sheet",NS):
            link=sheet.get("{"+NS["r"]+"}id")
            if link not in targets:raise LegacyValidationError("Aba com relacionamento externo ou ausente.")
            path=target_path("xl/workbook.xml",targets[link]);root=xml_part(archive,path)
            cells={};anchors={}
            for cell in root.findall("s:sheetData/s:row/s:c",NS):
                ref=cell.get("r")
                if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}",ref or ""):raise LegacyValidationError("Endereço de célula inválido.")
                kind=cell.get("t","n");raw=cell.find("s:v",NS);formula=cell.find("s:f",NS)
                value=raw.text if raw is not None else None
                if kind=="s" and value is not None:
                    try:decoded=strings[int(value)]
                    except (IndexError,ValueError) as exc:raise LegacyValidationError("Texto compartilhado inválido.") from exc
                elif kind=="inlineStr":decoded="".join(x.text or "" for x in cell.findall(".//s:t",NS))
                elif kind=="b":
                    if value not in {"0","1",None}:raise LegacyValidationError("Valor booleano inválido.")
                    decoded=value=="1" if value is not None else None
                else:decoded=value
                payload={"address":ref,"type":kind,"style":cell.get("s"),"raw_value":value,"value":decoded,
                    "formula":formula.text if formula is not None else None,
                    "formula_attributes":dict(formula.attrib) if formula is not None else None}
                if formula is not None and formula.get("t")=="shared" and formula.text:
                    anchors[formula.get("si")]=formula.text
                cells[ref]=payload
            for payload in cells.values():
                attrs=payload["formula_attributes"]
                if attrs and attrs.get("t")=="shared" and payload["formula"] is None:
                    payload["shared_anchor_formula"]=anchors.get(attrs.get("si"))
            sheets[sheet.get("name")]=cells
            relpath=posixpath.join(posixpath.dirname(path),"_rels",posixpath.basename(path)+".rels")
            if relpath in archive.namelist():
                links={r.get("Id"):r for r in xml_part(archive,relpath)}
                for part in root.findall("s:tableParts/s:tablePart",NS):
                    relation=links.get(part.get("{"+NS["r"]+"}id"))
                    if relation is None or relation.get("TargetMode")=="External":raise LegacyValidationError("Tabela externa não é aceita.")
                    table=xml_part(archive,target_path(path,relation.get("Target")))
                    if table.get("displayName")=="Fluxo_de_Caixa":
                        if table_sheet is not None:raise LegacyValidationError("Nome da tabela duplicado.")
                        table_sheet=sheet.get("name");table_ref=table.get("ref")
                        headers=[c.get("name") for c in table.findall("s:tableColumns/s:tableColumn",NS)]
        if table_sheet is None or headers!=EXPECTED_HEADERS:
            raise LegacyValidationError("A tabela Fluxo_de_Caixa não tem as 14 colunas esperadas.")
        match=re.fullmatch(r"B2:O([0-9]+)",table_ref or "")
        if not match or not 2<=int(match[1])<=50002:
            raise LegacyValidationError("Faixa da tabela fora do contrato de importação.")
        last=int(match[1])
        return {"sha256":sha256(content).hexdigest(),"date1904":date1904,"sheet":table_sheet,"table_ref":table_ref,
            "headers":headers,"sheets":sheets,"rows":[{"row_number":i,"cells":{chr(col):sheets[table_sheet].get(f"{chr(col)}{i}") for col in range(ord("B"),ord("O")+1)}} for i in range(3,last+1)]}

def numeric(cell):
    if not cell or cell["type"] not in {"n",""} or cell["value"] is None:return None
    try:
        if len(str(cell["value"]))>128:return None
        value=Decimal(cell["value"])
        return value if value.is_finite() and abs(value)<=Decimal("1e18") else None
    except InvalidOperation:return None

def arithmetic_formula(expression):
    """Apenas constantes e quatro operações; nenhum nome, função ou referência."""
    expression=expression.lstrip("=")
    if len(expression)>500:raise LegacyValidationError("Fórmula excede o limite.")
    try:tree=ast.parse(expression,mode="eval")
    except SyntaxError as exc:raise LegacyValidationError("Fórmula não é aritmética constante.") from exc
    if len(list(ast.walk(tree)))>100:raise LegacyValidationError("Fórmula excede o limite.")
    def evaluate(node):
        if isinstance(node,ast.Constant) and type(node.value) in {int,float}:
            token=ast.get_source_segment(expression,node)
            if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",token or ""):
                raise LegacyValidationError("Constante não suportada.")
            value=Decimal(token)
            if not value.is_finite() or abs(value)>Decimal("1e18"):raise LegacyValidationError("Constante fora do limite.")
            return value
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=evaluate(node.operand);return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node,ast.BinOp):
            a=evaluate(node.left);b=evaluate(node.right)
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
            if isinstance(node.op,ast.Div) and b:return a/b
        raise LegacyValidationError("Fórmula não é aritmética constante suportada.")
    with localcontext() as context:
        context.prec=60
        return evaluate(tree.body)

def excel_date(cell,date1904):
    if cell and cell["type"]=="d":
        try:return date.fromisoformat(cell["value"])
        except (ValueError,TypeError):return None
    serial=numeric(cell)
    if serial is None or serial!=serial.to_integral_value():return None
    number=int(serial)
    if not date1904 and number==60:return None
    try:
        epoch=date(1904,1,1) if date1904 else date(1899,12,30) if number>=61 else date(1899,12,31)
        return epoch+timedelta(days=number)
    except (OverflowError,ValueError):return None

def source_value(row,col):
    cell=row["cells"].get(col)
    return cell["value"] if cell else None

def prepare_rows(workbook):
    rows=workbook["rows"];by_number={r["row_number"]:r for r in rows}
    approved=workbook["sha256"]==APPROVED_SOURCE_SHA256
    if approved:
        if workbook["sheet"]!="Banco de dados" or workbook["table_ref"]!="B2:O549":
            raise LegacyValidationError("A fonte aprovada não tem a estrutura registrada.")
        keep,duplicate=by_number[125],by_number[129]
        if any(source_value(keep,c)!=source_value(duplicate,c) for c in "BCDEFGHIJKL"):
            raise LegacyValidationError("D12 não pôde ser aplicada: o conteúdo duplicado não confere.")
    output=[]
    for row in rows:
        cells=row["cells"];number=row["row_number"];issues=[];blockers=[]
        raw_amount=numeric(cells.get("G"));amount=raw_amount
        cell=cells.get("G") or {};formula=cell.get("formula") or cell.get("shared_anchor_formula")
        if cell.get("formula_attributes") is not None:
            try:
                if not formula or raw_amount is None:raise LegacyValidationError("Fórmula sem cache ou expressão.")
                exact=arithmetic_formula(formula)
                if abs(exact-raw_amount)>Decimal("0.00000001"):
                    raise LegacyValidationError("Resultado da fórmula diverge do cache.")
                amount=exact
                if exact!=raw_amount:issues.append("formula_exact_decimal")
            except (LegacyValidationError,InvalidOperation,OverflowError):
                blockers.append("unverified_value_formula")
        cents=None
        if amount is None or amount<=0 or amount>Decimal(2**53-1)/100:blockers.append("invalid_amount")
        elif amount*100!=(amount*100).to_integral_value():
            nearest=amount.quantize(Decimal("0.01"))
            blockers.append("binary_representation_review" if abs(amount-nearest)<=Decimal("0.00000001") else "fractional_cent")
        else:cents=int(amount*100)
        reference=excel_date(cells.get("B"),workbook["date1904"])
        if reference is None:blockers.append("invalid_reference_date")
        raw_description=source_value(row,"C");description=raw_description.strip() if isinstance(raw_description,str) else None
        if not description:issues.append("missing_description");description=None
        category=source_value(row,"F")
        if category is None or not str(category).strip():issues.append("missing_category")
        kind={"despesa":"expense","receita":"income"}.get(normalized(source_value(row,"I")))
        responsible={"pessoal":"self","pai/mae":"parents"}.get(normalized(source_value(row,"H")))
        method={"credito":"credit","pix":"pix","dinheiro":"cash"}.get(normalized(source_value(row,"J")))
        if kind is None:blockers.append("unknown_kind")
        if responsible is None:blockers.append("unknown_responsible")
        if method is None:blockers.append("unknown_method")
        bank=source_value(row,"K")
        card={"nubank":"nubank","mercado livre":"mercado_pago","mercado pago":"mercado_pago","mercado pago/livre":"mercado_pago"}.get(normalized(bank)) if method=="credit" else None
        if method=="credit" and (card is None or kind!="expense"):blockers.append("unknown_card_or_credit_income")
        if normalized(bank)=="pix":issues.append("institution_unknown")
        if kind=="income" and normalized(description)=="conta banco":blockers.append("income_or_opening_balance")
        count=numeric(cells.get("E"));part=numeric(cells.get("D"))
        if count is None or part is None or count!=count.to_integral_value() or part!=part.to_integral_value() or not 1<=part<=count<=100000:
            blockers.append("invalid_installment_metadata")
        elif count>1:issues.append("sequence_not_reconstructed")
        paid=source_value(row,"L")
        if type(paid) is not bool:blockers.append("unknown_paid_flag")
        if responsible=="parents":issues.append("parents_settlement_unknown")
        if method!="credit":issues.append("noncredit_settlement_unknown")
        if approved and number in {174,236,345}:issues.append("D13_independent_observation")
        status="excluded" if approved and number==129 else "pending" if blockers else "accepted"
        if status=="excluded":issues.append("D12_duplicate_of_row_125")
        simulated_cycle=None
        if card and reference:
            configured=NUBANK if card=="nubank" else MERCADO_PAGO
            try:simulated_cycle=first_invoice_cycle(reference,configured).closing_date.replace(day=1).isoformat()
            except ValueError:blockers.append("cycle_out_of_range");status="pending"
            issues.append("cycle_simulation_requires_reconciliation")
        proposal={"description":description,"normalized_description":normalized(description),"reference_on":reference.isoformat() if reference else None,
            "kind":kind,"responsible_code":responsible,"payment_method":method,"card_code":card,"bank_original":bank,
            "category_original":category,"amount_cents":cents,"observed_number":int(part) if part is not None and part==part.to_integral_value() and 0<=part<=100000 else None,
            "observed_count":int(count) if count is not None and count==count.to_integral_value() and 0<=count<=100000 else None,"paid_evidence":paid if type(paid) is bool else None,
            "simulated_cycle":simulated_cycle}
        output.append({"row_number":number,"sheet_name":workbook["sheet"],"status":status,"issues":issues+blockers,"blockers":blockers,
            "source_amount_decimal":decimal_text(raw_amount),"resolved_amount_decimal":decimal_text(amount),
            "proposal":proposal,"raw":row,"representation_difference":decimal_text(raw_amount-Decimal(cents)/100) if raw_amount is not None and cents is not None else None})
    return output

def controls(rows):
    """Controles exatos por data original, responsabilidade, cartão e tipo."""
    with localcontext() as context:
        context.prec=60
        zero=Decimal(0);source=zero;pending=zero;excluded=zero;representation=zero;accepted=0;missing=0
        groups={}
        counts={status:0 for status in ("accepted","pending","excluded")}
        issues={}
        for row in rows:
            status=row["status"];counts[status]+=1
            for issue in row["issues"]:issues[issue]=issues.get(issue,0)+1
            p=row["proposal"]
            group_key=document([p["reference_on"][:7] if p["reference_on"] else None,p["kind"],p["responsible_code"],p["card_code"] or p["payment_method"]])
            group=groups.setdefault(group_key,{"source_rows":0,"accepted_rows":0,"accepted_cents":0,"source_decimal":zero,"pending_decimal":zero,"excluded_decimal":zero,"representation_decimal":zero})
            group["source_rows"]+=1
            raw=Decimal(row["source_amount_decimal"]) if row["source_amount_decimal"] is not None else None
            if raw is None:missing+=1
            else:
                source+=raw;group["source_decimal"]+=raw
                if status=="pending":pending+=raw;group["pending_decimal"]+=raw
                elif status=="excluded":excluded+=raw;group["excluded_decimal"]+=raw
            if status=="accepted":
                accepted+=p["amount_cents"];group["accepted_cents"]+=p["amount_cents"];group["accepted_rows"]+=1
                difference=raw-Decimal(p["amount_cents"])/100
                representation+=difference;group["representation_decimal"]+=difference
        for group in groups.values():
            group["difference_decimal"]=decimal_text(group["source_decimal"]-Decimal(group["accepted_cents"])/100-group["pending_decimal"]-group["excluded_decimal"]-group["representation_decimal"])
            for key,value in list(group.items()):
                if isinstance(value,Decimal):group[key]=decimal_text(value)
        difference=source-Decimal(accepted)/100-pending-excluded-representation
        return {"source_rows":len(rows),"counts":counts,"source_decimal":decimal_text(source),"accepted_cents":accepted,
            "pending_decimal":decimal_text(pending),"excluded_decimal":decimal_text(excluded),"representation_decimal":decimal_text(representation),
            "difference_decimal":decimal_text(difference),"non_numeric_source_amounts":missing,"issues":issues,"groups":groups,
            "reconciled":difference==0 and all(Decimal(g["difference_decimal"])==0 for g in groups.values()) and missing==0}
