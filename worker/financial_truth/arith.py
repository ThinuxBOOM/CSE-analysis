"""
Exact Decimal arithmetic for OP1 and intervals: a local context with 80 digits that traps inexact results (as F6.1
does), and copy_abs / copy_negate, which never round. Binary floating point is never used.
"""
from decimal import Decimal, Inexact, localcontext


def add(*xs):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        out = Decimal(0)
        for x in xs:
            out = out + x
        return out


def sub(a, b):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        return a - b


def abs_diff(a, b):
    return sub(a, b).copy_abs()


def negate(a):
    return a.copy_negate()
