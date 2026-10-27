import pytest
from app.domain.settlements import effects,cash_direction,validate_capacity
from app.domain.errors import FinancialValidationError

@pytest.mark.parametrize("kind,person,dimensions,direction",[("invoice_payment","self",("payment",),"out"),("invoice_payment","parents",("payment","parents"),"none"),("repayment","parents",("parents",),"in"),("income_receipt",None,("payment",),"in"),("expense_payment","parents",("payment","parents"),"none")])
def test_dimensions_and_own_cash_are_independent(kind,person,dimensions,direction):
    assert effects(kind,person)==dimensions and cash_direction(kind,person)==direction

@pytest.mark.parametrize("kind,person",[("repayment","self"),("income_receipt","self"),("invoice_payment",None),("unknown","parents")])
def test_invalid_roles(kind,person):
    with pytest.raises(FinancialValidationError):effects(kind,person)

@pytest.mark.parametrize("values",[(True,0,100),(1.5,0,100),(101,0,100),(1,100,100),(0,0,100),(-1,0,100)])
def test_capacity_rejects_invalid_cent_values(values):
    with pytest.raises(FinancialValidationError):validate_capacity(*values)

def test_partial_capacity_exact():
    assert validate_capacity(40000,0,150000) is None
    assert validate_capacity(110000,40000,150000) is None
