import ast
import operator

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("calculator", streamable_http_path="/", instructions=(
    "Always call the calculate tool for any arithmetic — never compute in your head. "
    "This ensures accuracy and lets the result be verified."
))

class CalcResult(BaseModel):
    expression: str
    result: float

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")

@mcp.tool()
def calculate(expression: str) -> CalcResult:
    return CalcResult(expression=expression, result=_eval(ast.parse(expression, mode="eval")))
