"""
MF's arithmetic (design section 10.1, P3-D7): Python Decimal in one fixed context (precision 28, half-even), ln, exp
and sqrt from `decimal`, results quantized to 12 decimal places. No float, so the same inputs give the same bytes on
every platform. Nothing is annualised.

`simple_return` is the one return primitive: the feature path (return history) and the target path (labels) both
import it from here. A shared arithmetic primitive is not a label.
"""
from decimal import ROUND_HALF_EVEN, Context, Decimal
from fractions import Fraction

CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
QUANTUM = Decimal("0.000000000001")


def to_decimal(value) -> Decimal:
    """An exact rational, an int or a Decimal, as a Decimal in MF's context."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("MF's arithmetic takes no float and no bool")
    if isinstance(value, Fraction):
        return CTX.divide(Decimal(value.numerator), Decimal(value.denominator))
    return CTX.create_decimal(value)


def q(value: Decimal) -> Decimal:
    """The stored form of a result: 12 decimal places, half-even."""
    return value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN, context=CTX)


def ln(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("ln of a non-positive value")
    return CTX.ln(value)


def exp(value: Decimal) -> Decimal:
    return CTX.exp(value)


def sqrt(value: Decimal) -> Decimal:
    return CTX.sqrt(value)


def add(*values) -> Decimal:
    out = Decimal(0)
    for v in values:
        out = CTX.add(out, v)
    return out


def mean(values) -> Decimal:
    values = list(values)
    return CTX.divide(add(*values), Decimal(len(values)))


def simple_return(end_price: Decimal, start_price: Decimal) -> Decimal:
    """c_end / c_start - 1, unquantized."""
    if start_price <= 0 or end_price <= 0:
        raise ValueError("a return needs two positive prices")
    return CTX.subtract(CTX.divide(end_price, start_price), Decimal(1))


def log_return(end_price: Decimal, start_price: Decimal) -> Decimal:
    if start_price <= 0 or end_price <= 0:
        raise ValueError("a return needs two positive prices")
    return ln(CTX.divide(end_price, start_price))
