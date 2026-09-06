"""Arithmetic in the numeric fields, without handing them to eval.

Material comes in inches and the machine works in millimetres, so a field that
accepts ``25.4/2`` saves a trip to a calculator. What it must not accept is
anything else: these fields are user input, and eval on user input would run
whatever was pasted into them.

Only numbers, the four operations, a percent sign and parentheses get through.
Anything else is a refusal, not an attempt to be clever.
"""
import ast
import math
import operator

OPERATIONS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
MAX_LENGTH = 60


def evaluate(text, percent_of=None):
    """The number a field holds, after working out any arithmetic in it.

    ``percent_of`` gives ``50%`` a meaning: half of that value. Without it a
    percent sign is refused rather than quietly treated as a hundredth.
    """
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).strip()
    if not text:
        raise ValueError("Enter a number.")
    if len(text) > MAX_LENGTH:
        raise ValueError("That expression is too long.")
    if text.endswith("%"):
        if percent_of is None:
            raise ValueError("A percentage means nothing in this field.")
        return evaluate(text[:-1]) / 100.0 * percent_of
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ValueError(f"{text!r} is not a number or a sum.") from None
    value = _reduce(tree.body, text)
    if not math.isfinite(value):
        raise ValueError("That works out to a number the machine cannot use.")
    return float(value)


def _reduce(node, text):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"{text!r} is not a number or a sum.")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATIONS:
        left, right = _reduce(node.left, text), _reduce(node.right, text)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("That divides by zero.")
        if isinstance(node.op, ast.Pow) and (abs(left) > 1000 or abs(right) > 8):
            raise ValueError("That power is too large to be useful here.")
        return OPERATIONS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATIONS:
        return OPERATIONS[type(node.op)](_reduce(node.operand, text))
    # Names, calls, attributes, subscripts: all refused rather than resolved.
    raise ValueError(f"{text!r} is not a number or a sum.")
