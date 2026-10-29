"""Bounded arithmetic evaluation; never executes code from a memory document."""

import ast
import math
import operator

from langchain_core.tools import tool

OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def calculate(expression: str, values: dict[str, float]) -> float:
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 80:
        raise ValueError("计算表达式过长。")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in [int, float]:
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in values:
            return float(values[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1e12):
                raise ValueError("指数超出支持范围。")
            result = float(OPS[type(node.op)](left, right))
            if not math.isfinite(result) or abs(result) > 1e100:
                raise ValueError("计算结果超出范围。")
            return result
        raise ValueError("只支持数值、变量和 + - * / // % ** 运算。")

    return evaluate(tree)


@tool
def calculate_expression(expression: str, values: dict[str, float]) -> str:
    """Evaluate a saved arithmetic formula with explicitly supplied numeric variables."""
    return str(calculate(expression, values))
