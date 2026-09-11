"""Versioned expert-source checks. No mutation of existing imports or catalogs."""
import ast
from datetime import datetime, timedelta
import math
import operator

LIMS_NAME = 'LIMS:24-2000.Pipeline.95%.T'
SOURCE_SHA = '64b10e81978a540bec84039927f73aa98f2d95d99bc7bc349f5f4d5f68842110'


def publication_gate(kind, sample, origin, *, published=None, received=None,
                     use_reported_upper_bound=False, source_sha=None):
    """received is an optional later receipt; never make it earlier availability."""
    times = [datetime.fromisoformat(t) for t in (sample, origin, published, received) if t is not None]
    if any(t.tzinfo is not None for t in times): raise ValueError('Use common source-local naive clock; UTC offset not established')
    event, at = times[:2]
    actual = datetime.fromisoformat(published) if published is not None else None
    receipt = datetime.fromisoformat(received) if received is not None else None
    if kind not in ('lims','pac'): raise ValueError('Unknown source kind')
    if actual is not None and actual < event: raise ValueError('Publication before event')
    if receipt is not None and receipt < event: raise ValueError('Receipt before event')
    if actual is not None:
        eligible, basis = actual, 'explicit_publication'
    elif kind == 'lims' and use_reported_upper_bound and source_sha == SOURCE_SHA:
        eligible, basis = event+timedelta(hours=4), 'reported_upper_bound_not_observed_publication'
    else:
        return {'allowed': False, 'reason': 'AVAILABILITY_UNKNOWN', 'actual_publication': None, 'eligibility_time': None}
    if receipt is not None: eligible = max(eligible, receipt)
    return {'allowed': event <= at and eligible <= at, 'reason': 'ELIGIBLE' if event <= at and eligible <= at else 'NOT_YET_AVAILABLE',
            'actual_publication': actual.isoformat() if actual else None, 'eligibility_time': eligible.isoformat(), 'basis': basis}


def evaluate_formula(expression, values, *, lims_evidence=None):
    """Arithmetic-only AST; no eval, functions, attributes or implicit imputation."""
    normalized = expression.replace(LIMS_NAME, 'LIMS_T95')
    tree = ast.parse(normalized, mode='eval')
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
    def number(value):
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value): raise ValueError('Nonfinite/non-numeric input')
        return float(value)
    def visit(node):
        if isinstance(node, ast.Expression): return visit(node.body)
        if isinstance(node, ast.Constant): return number(node.value)
        if isinstance(node, ast.Name):
            name = LIMS_NAME if node.id == 'LIMS_T95' else node.id
            if name == LIMS_NAME and not (lims_evidence and lims_evidence.get('allowed') is True): raise ValueError('LIMS dependency unavailable')
            if name not in values: raise ValueError('Missing variable: '+name)
            return number(values[name])
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            try: return number(binary[type(node.op)](visit(node.left),visit(node.right)))
            except ZeroDivisionError as exc: raise ValueError('Zero denominator') from exc
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            return number(visit(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1))
        raise ValueError('Unsupported formula syntax')
    return visit(tree)
