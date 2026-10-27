"""Prévia de reconstrução: decisões explícitas, sem gravar eventos ou adotar banco."""
from copy import deepcopy
from collections import Counter, defaultdict
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from .legacy_reader import LegacyValidationError

POLICY = "history-reconstruction-preview-v3"

def apply_decisions(rows, binary_approvals):
    """A lista liga cada linha ao decimal de origem e ao centavo aprovado."""
    result = deepcopy(rows)
    found = set()
    for row in result:
        number = row["row_number"]
        if number in binary_approvals:
            original, cents = binary_approvals[number]
            if "binary_representation_review" not in row["blockers"] or Decimal(row["source_amount_decimal"]) != Decimal(original):
                raise LegacyValidationError("A aprovação não corresponde ao valor de origem.")
            value = Decimal(original)
            nearest = (value * 100).to_integral_value(rounding=ROUND_HALF_EVEN)
            if type(cents) is not int or nearest != cents or abs(value - Decimal(cents)/100) > Decimal("0.000000001"):
                raise LegacyValidationError("Valor fora da regra de precisão aprovada.")
            row["proposal"]["amount_cents"] = cents
            row["blockers"].remove("binary_representation_review")
            row["approved_transformation"] = "limited_binary_precision"
            found.add(number)
        elif number in (421, 477):
            if Decimal(row["source_amount_decimal"]) != Decimal("61.125") or "fractional_cent" not in row["blockers"]:
                raise LegacyValidationError("A origem das parcelas fracionárias mudou.")
            row["proposal"]["amount_cents"] = 6113 if number == 421 else 6112
            row["blockers"].remove("fractional_cent")
            row["approved_transformation"] = "explicit_installment_distribution"
        if number == 44:
            if Decimal(row["source_amount_decimal"]) != Decimal("420.00") or "income_or_opening_balance" not in row["blockers"]:
                raise LegacyValidationError("A origem do saldo inicial mudou.")
            row["blockers"].remove("income_or_opening_balance")
            if (row["proposal"]["reference_on"] != "2025-01-03"
                    or row["proposal"].get("bank_original") != "Nubank"):
                raise LegacyValidationError("A conta ou data do saldo inicial diverge da confirmação.")
            row["role"] = "opening_balance"
            row["opening_balance"] = {"bank_code":"nubank", "as_of":"2025-01-03",
                                      "amount_cents":42000, "confirmed_by_user":True}
        else:
            row["role"] = "observed_obligation"
        if row["status"] != "excluded":
            row["status"] = "pending" if row["blockers"] else "prepared"
        row["payment_pending"] = row["proposal"]["paid_evidence"] is False
        row["cash_event_created"] = False
    if found != set(binary_approvals):
        raise LegacyValidationError("Há aprovação sem origem correspondente.")
    return result

def sequence_candidates(rows):
    """Propor sequências completas, únicas e sem reutilizar origem; não aprová-las."""
    buckets = defaultdict(list)
    fields = ("normalized_description", "kind", "responsible_code", "payment_method",
              "card_code", "bank_original", "category_original", "observed_count")
    for row in rows:
        p = row["proposal"]
        if (row["status"] == "excluded" or p["observed_count"] <= 1
                or not p["normalized_description"] or row["row_number"] in (174,236,345)):
            continue
        buckets[tuple(p.get(f) for f in fields) + (p["amount_cents"],)].append(row)
    candidates = []
    for group in buckets.values():
        for first in group:
            p = first["proposal"]
            if p["observed_number"] != 1:
                continue
            start = date.fromisoformat(p["reference_on"])
            matches = []
            for number in range(1, p["observed_count"]+1):
                year, month = divmod(start.year*12+start.month+number-2, 12)
                expected = date(year, month+1, min(start.day, monthrange(year,month+1)[1])).isoformat()
                found = [r for r in group if r["proposal"]["observed_number"] == number
                         and r["proposal"]["reference_on"] == expected]
                if len(found) != 1:
                    break
                matches.append(found[0]["row_number"])
            if len(matches) == p["observed_count"]:
                candidates.append(matches)
    usage = Counter(number for group in candidates for number in group)
    return [group for group in candidates if all(usage[number] == 1 for number in group)]
