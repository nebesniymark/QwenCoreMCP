#!/usr/bin/env python3
"""
MCP math server v3.0.0

Three evaluation backends, none of which uses eval()/exec() on user input:
  * numeric  - strict AST evaluator (ints/floats, math + finance functions)
  * symbolic - AST evaluator that builds SymPy objects from a whitelist
  * matrix   - AST evaluator over NumPy arrays from a whitelist

Safety limits: expression length, AST node count, integer bit length,
exponent size, factorial argument, array size, and a wall-clock timeout for
the heavy (SymPy/NumPy) backends.

Finance convention: rates are DECIMAL PER PERIOD (0.01 = 1% per period).
Use monthly(12) to turn "12% annual" into a monthly rate.
"""
import ast
import difflib
import json
import math
import operator
import sys
import threading

try:
    import sympy as sp
except ImportError:  # pragma: no cover
    sp = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

SERVER_NAME = "math-calculator"
SERVER_VERSION = "3.0.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2024-11-05"

MAX_EXPR_LEN = 2000
MAX_AST_NODES = 180
MAX_INT_BITS = 4096
MAX_EXPONENT = 500
MAX_FACTORIAL_ARG = 1000
MAX_ARRAY_ELEMENTS = 10_000
MAX_PERIODS = 12_000
HEAVY_TIMEOUT = 10.0  # seconds, symbolic and matrix modes

MODES = ("auto", "numeric", "symbolic", "matrix")


# ------------------------------ Finance helpers ------------------------------

def check_rate(rate, name="rate"):
    if not -1 < rate < 1:
        raise ValueError(
            f"{name} must be a decimal PER PERIOD between -1 and 1 (0.01 = 1%). "
            "For 12% annual with monthly payments use monthly(12) or 0.12/12"
        )
    return rate


def check_nper(nper):
    if not 0 < nper <= MAX_PERIODS:
        raise ValueError(f"nper must be between 0 and {MAX_PERIODS}")
    return nper


def monthly(annual_percent):
    """12 (percent per year) -> 0.01 (decimal per month)."""
    return annual_percent / 100.0 / 12.0


def pct(percent):
    """5 -> 0.05"""
    return percent / 100.0


def pmt(rate, nper, pv):
    r, n = check_rate(rate), check_nper(nper)
    if r == 0:
        return pv / n
    return pv * r / (1 - (1 + r) ** -n)


def fv(rate, nper, pmt_val, pv=0):
    r, n = check_rate(rate), check_nper(nper)
    if r == 0:
        return pv + pmt_val * n
    growth = (1 + r) ** n
    return pv * growth + pmt_val * (growth - 1) / r


def fv_growing_rent(invest_rate, nper, pmt_mortgage, rent_start, rent_growth_annual):
    """Monthly loop: invest the difference between mortgage payment and rent.

    invest_rate is per month (decimal); rent_growth_annual is a yearly decimal.
    """
    r = check_rate(invest_rate, "invest_rate")
    check_rate(rent_growth_annual, "rent_growth_annual")
    n = check_nper(nper)
    balance = 0.0
    rent = float(rent_start)
    for month in range(1, int(n) + 1):
        if month > 1 and (month - 1) % 12 == 0:
            rent *= 1 + rent_growth_annual
        balance = balance * (1 + r) + (pmt_mortgage - rent)
    return balance


def npv(rate, *cashflows):
    r = check_rate(rate)
    if not cashflows:
        raise ValueError("npv needs at least one cashflow")
    return sum(cf / ((1 + r) ** i) for i, cf in enumerate(cashflows))


def irr(*cashflows):
    """Per-period internal rate of return (root closest to 0)."""
    if len(cashflows) < 2:
        raise ValueError("irr needs at least two cashflows")
    if not (any(c < 0 for c in cashflows) and any(c > 0 for c in cashflows)):
        raise ValueError("irr needs at least one negative and one positive cashflow")

    def f(r):
        try:
            return sum(cf / ((1 + r) ** i) for i, cf in enumerate(cashflows))
        except ArithmeticError:
            return None

    grid = [-0.99 + 0.01 * k for k in range(200)] + [1.0 + 0.5 * k for k in range(19)]
    roots = []
    prev_r, prev_v = None, None
    for r in grid:
        v = f(r)
        if v is None:
            prev_r, prev_v = None, None
            continue
        if v == 0:
            roots.append(r)
        elif prev_v is not None and prev_v * v < 0:
            lo, hi, flo = prev_r, r, prev_v
            for _ in range(200):
                mid = (lo + hi) / 2
                fm = f(mid)
                if fm is None:
                    break
                if fm == 0 or (hi - lo) < 1e-13:
                    lo = hi = mid
                    break
                if flo * fm < 0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            roots.append((lo + hi) / 2)
        prev_r, prev_v = r, v
    if not roots:
        raise ValueError("IRR not found in the range -99%..1000% per period")
    return min(roots, key=abs)


# ------------------------------ Numeric backend ------------------------------

OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
UOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_factorial(n):
    if type(n) is not int or not 0 <= n <= MAX_FACTORIAL_ARG:
        raise ValueError(f"factorial argument must be an integer from 0 to {MAX_FACTORIAL_ARG}")
    return math.factorial(n)


NUMERIC_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2, "log": math.log, "log2": math.log2,
    "log10": math.log10, "exp": math.exp, "floor": math.floor,
    "ceil": math.ceil, "factorial": safe_factorial, "gcd": math.gcd,
    "lcm": math.lcm, "hypot": math.hypot, "degrees": math.degrees,
    "radians": math.radians,
    "monthly": monthly, "pct": pct,
    "pmt": pmt, "fv": fv, "fv_growing_rent": fv_growing_rent,
    "npv": npv, "irr": irr,
}
CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
FUNC_ALIASES = {
    "future_val": "fv", "future_value": "fv", "annuity": "pmt", "payment": "pmt",
    "ln": "log", "power": "pow", "average": "mean",
}


def preprocess(expr):
    return expr.replace("^", "**")


def parse_tree(expr):
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("Expression must be a non-empty string")
    if len(expr) > MAX_EXPR_LEN:
        raise ValueError("Expression is too long")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise ValueError("Invalid expression syntax") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise ValueError("Expression is too complex")
    return tree


def checked(value):
    t = type(value)
    if t is int:
        if value.bit_length() > MAX_INT_BITS:
            raise ValueError("Integer result too large (exceeds bit limit)")
    elif t is float:
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric result")
    else:
        raise ValueError("Result is not a real number")
    return value


def call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id in ("math", "np", "numpy", "sympy", "sp")):
        return func.attr
    raise ValueError("Only approved function calls are allowed")


def safe_call_name(func):
    try:
        return call_name(func)
    except ValueError:
        return None


def unknown_function(name, available):
    hint = ""
    target = FUNC_ALIASES.get(name)
    if target is None:
        close = difflib.get_close_matches(name, list(available), n=1, cutoff=0.6)
        target = close[0] if close else None
    if target:
        hint = f" Did you mean '{target}'?"
    return ValueError(f"Unknown function '{name}'.{hint}")


def numeric_eval(node):
    if isinstance(node, ast.Expression):
        return numeric_eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return checked(node.value)
    if isinstance(node, ast.Name):
        if node.id in CONSTS:
            return CONSTS[node.id]
        raise ValueError(f"Unknown name '{node.id}'. Variables need symbolic mode (x, y, z, t)")
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "math" and node.attr in CONSTS):
        return CONSTS[node.attr]
    if isinstance(node, ast.UnaryOp) and type(node.op) in UOPS:
        return checked(UOPS[type(node.op)](numeric_eval(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        a, b = numeric_eval(node.left), numeric_eval(node.right)
        if type(node.op) is ast.Pow:
            if abs(b) > MAX_EXPONENT:
                raise ValueError(f"Exponent too large (max allowed exponent is {MAX_EXPONENT})")
            if type(a) is int and type(b) is int and b > 0 and a.bit_length() * b > MAX_INT_BITS:
                raise ValueError("Integer result too large for exponentiation")
        try:
            return checked(OPS[type(node.op)](a, b))
        except ZeroDivisionError:
            raise ValueError("Division by zero") from None
        except OverflowError:
            raise ValueError("Numeric overflow") from None
    if isinstance(node, ast.Call):
        name = call_name(node.func)
        fn = NUMERIC_FUNCS.get(name)
        if fn is None:
            raise unknown_function(name, NUMERIC_FUNCS)
        args = [numeric_eval(arg) for arg in node.args]
        kwargs = {}
        for kw in node.keywords:
            if name != "fv" or kw.arg != "pv":
                raise ValueError("Keyword arguments are only allowed for fv(..., pv=...)")
            kwargs[kw.arg] = numeric_eval(kw.value)
        try:
            return checked(fn(*args, **kwargs))
        except Exception as exc:
            raise ValueError(f"Error in {name}: {exc}") from exc
    raise ValueError("Expression element not allowed in numeric mode")


def format_number(v):
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return format(v, ".15g")
    return str(v)


# ------------------------------ Symbolic backend -----------------------------

SYM_VARS = ("x", "y", "z", "t")
_SYM_CACHE = {}


def sym_env():
    if sp is None:
        raise RuntimeError("SymPy is not installed (pip install sympy)")
    if not _SYM_CACHE:
        x, y, z, t = sp.symbols("x y z t")
        _SYM_CACHE["names"] = {
            "x": x, "y": y, "z": z, "t": t,
            "pi": sp.pi, "e": sp.E, "E": sp.E, "oo": sp.oo, "I": sp.I,
        }
        _SYM_CACHE["funcs"] = {
            "solve": sp.solve, "diff": sp.diff, "integrate": sp.integrate,
            "simplify": sp.simplify, "factor": sp.factor, "expand": sp.expand,
            "limit": sp.limit, "Eq": sp.Eq, "Rational": sp.Rational,
            "sqrt": sp.sqrt, "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
            "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
            "exp": sp.exp, "log": sp.log, "abs": sp.Abs, "Abs": sp.Abs,
        }
    return _SYM_CACHE["names"], _SYM_CACHE["funcs"]


SYM_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)


def check_sym_pow(a, b):
    if isinstance(b, sp.Basic) and b.is_number:
        try:
            fb = abs(float(b))
        except TypeError:
            raise ValueError("Complex exponents are not supported") from None
        if fb > MAX_EXPONENT:
            raise ValueError(f"Exponent too large (max allowed exponent is {MAX_EXPONENT})")
        if (isinstance(a, sp.Integer) and isinstance(b, sp.Integer) and b > 0
                and int(a).bit_length() * int(b) > MAX_INT_BITS):
            raise ValueError("Integer result too large for exponentiation")


def sym_eval(node):
    names, funcs = sym_env()
    if isinstance(node, ast.Expression):
        return sym_eval(node.body)
    if isinstance(node, ast.Constant):
        v = node.value
        if type(v) is int:
            if v.bit_length() > MAX_INT_BITS:
                raise ValueError("Integer too large")
            return sp.Integer(v)
        if type(v) is float:
            if not math.isfinite(v):
                raise ValueError("Non-finite number")
            return sp.Float(v)
        raise ValueError("Only numbers are allowed")
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise ValueError(f"Unknown name '{node.id}'. Variables: x, y, z, t")
    if isinstance(node, ast.UnaryOp) and type(node.op) in UOPS:
        return UOPS[type(node.op)](sym_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in SYM_BINOPS:
        a, b = sym_eval(node.left), sym_eval(node.right)
        if type(node.op) is ast.Pow:
            check_sym_pow(a, b)
        return OPS[type(node.op)](a, b)
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [sym_eval(e) for e in node.elts]
        return items if isinstance(node, ast.List) else tuple(items)
    if isinstance(node, ast.Call):
        name = call_name(node.func)
        fn = funcs.get(name)
        if fn is None:
            raise unknown_function(name, funcs)
        if node.keywords:
            raise ValueError("Keyword arguments are not allowed in symbolic mode")
        return fn(*[sym_eval(a) for a in node.args])
    raise ValueError("Expression element not allowed in symbolic mode")


def split_equation(expr):
    """Return (left, right) for a single top-level '=', else None."""
    depth, idx = 0, None
    for i, ch in enumerate(expr):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "=" and depth == 0:
            prev = expr[i - 1] if i > 0 else ""
            nxt = expr[i + 1] if i + 1 < len(expr) else ""
            if prev in "<>=!" or nxt == "=":
                continue
            if idx is not None:
                raise ValueError("Only one '=' is allowed")
            idx = i
    return None if idx is None else (expr[:idx], expr[idx + 1:])


def format_sym(res):
    if isinstance(res, (list, tuple)):
        return ", ".join(sp.sstr(i) for i in res) if res else "[]"
    return sp.sstr(res)


def symbolic_calculate(expr):
    sym_env()
    try:
        parts = split_equation(expr)
        if parts is not None:
            left, right = (sym_eval(parse_tree(p)) for p in parts)
            eq = sp.Eq(left, right)
            if eq in (sp.true, sp.false):
                return str(bool(eq))
            symbols = sorted(eq.free_symbols, key=str)
            return format_sym(sp.solve(eq, symbols[0] if len(symbols) == 1 else symbols))
        return format_sym(sym_eval(parse_tree(expr)))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"SymPy evaluation error: {exc}") from exc


# ------------------------------- Matrix backend ------------------------------

def _arr(v):
    a = np.asarray(v)
    if np.iscomplexobj(a):
        raise ValueError("Complex values are only supported as a final result")
    a = a.astype(float)
    if a.size > MAX_ARRAY_ELEMENTS:
        raise ValueError(f"Array too large (max {MAX_ARRAY_ELEMENTS} elements)")
    return a


def _to_int(v):
    a = np.asarray(v, dtype=float)
    if a.ndim != 0 or not math.isfinite(float(a)) or float(a) != int(a):
        raise ValueError("Expected an integer")
    return int(a)


def _shape(v):
    a = np.asarray(v, dtype=float)
    dims = [_to_int(a)] if a.ndim == 0 else [_to_int(d) for d in a.reshape(-1)]
    if not dims or len(dims) > 4 or any(d < 0 for d in dims):
        raise ValueError("Invalid shape")
    if math.prod(dims) > MAX_ARRAY_ELEMENTS:
        raise ValueError(f"Array too large (max {MAX_ARRAY_ELEMENTS} elements)")
    return tuple(dims)


def _arange(start, stop=None, step=1.0):
    if stop is None:
        start, stop = 0.0, start
    start, stop, step = float(start), float(stop), float(step)
    if step == 0:
        raise ValueError("step must not be zero")
    if math.ceil((stop - start) / step) > MAX_ARRAY_ELEMENTS:
        raise ValueError(f"Array too large (max {MAX_ARRAY_ELEMENTS} elements)")
    return np.arange(start, stop, step)


def _eye(n):
    k = _to_int(n)
    if k < 0 or k * k > MAX_ARRAY_ELEMENTS:
        raise ValueError(f"Array too large (max {MAX_ARRAY_ELEMENTS} elements)")
    return np.eye(k)


def _matpow(a, n):
    k = _to_int(n)
    if not 0 <= k <= 100:
        raise ValueError("matpow exponent must be an integer from 0 to 100")
    return np.linalg.matrix_power(_arr(a), k)


def mat_funcs():
    la = np.linalg
    return {
        "array": lambda x: _arr(x),
        "det": lambda x: la.det(_arr(x)),
        "inv": lambda x: la.inv(_arr(x)),
        "eigvals": lambda x: la.eigvals(_arr(x)),
        "norm": lambda x: la.norm(_arr(x)),
        "solve": lambda a, b: la.solve(_arr(a), _arr(b)),
        "dot": lambda a, b: np.dot(_arr(a), _arr(b)),
        "matmul": lambda a, b: np.matmul(_arr(a), _arr(b)),
        "zeros": lambda s: np.zeros(_shape(s)),
        "ones": lambda s: np.ones(_shape(s)),
        "eye": _eye,
        "arange": _arange,
        "reshape": lambda a, s: np.reshape(_arr(a), _shape(s)),
        "transpose": lambda x: _arr(x).T,
        "trace": lambda x: np.trace(_arr(x)),
        "rank": lambda x: la.matrix_rank(_arr(x)),
        "matpow": _matpow,
    }


def mat_eval(node, funcs):
    if isinstance(node, ast.Expression):
        return mat_eval(node.body, funcs)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        try:
            return np.asarray(float(node.value))
        except OverflowError:
            raise ValueError("Number too large") from None
    if isinstance(node, ast.Name):
        if node.id in CONSTS:
            return np.asarray(CONSTS[node.id])
        raise ValueError(f"Unknown name '{node.id}'")
    if isinstance(node, ast.Attribute):
        if node.attr == "T":
            return _arr(mat_eval(node.value, funcs)).T
        if (isinstance(node.value, ast.Name) and node.value.id in ("math", "np", "numpy")
                and node.attr in CONSTS):
            return np.asarray(CONSTS[node.attr])
        raise ValueError("Attribute access is not allowed")
    if isinstance(node, ast.UnaryOp) and type(node.op) in UOPS:
        return UOPS[type(node.op)](_arr(mat_eval(node.operand, funcs)))
    if isinstance(node, ast.List):
        return _arr(np.array([mat_eval(e, funcs) for e in node.elts]))
    if isinstance(node, ast.Tuple):
        return tuple(mat_eval(e, funcs) for e in node.elts)
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        a, b = _arr(mat_eval(node.left, funcs)), _arr(mat_eval(node.right, funcs))
        if op is ast.Add:
            r = a + b
        elif op is ast.Sub:
            r = a - b
        elif op is ast.Mult:
            r = a * b
        elif op is ast.Div:
            r = a / b
        elif op is ast.MatMult:
            r = np.matmul(a, b)
        elif op is ast.Pow:
            if b.size and np.max(np.abs(b)) > MAX_EXPONENT:
                raise ValueError(f"Exponent too large (max allowed exponent is {MAX_EXPONENT})")
            r = np.power(a, b)  # elementwise, like NumPy; use matpow() for matrix power
        else:
            raise ValueError("Operator not allowed in matrix mode")
        return _arr(r)
    if isinstance(node, ast.Call):
        name = call_name(node.func)
        fn = funcs.get(name)
        if fn is None:
            raise unknown_function(name, funcs)
        if node.keywords:
            raise ValueError("Keyword arguments are not allowed in matrix mode")
        res = np.asarray(fn(*[mat_eval(a, funcs) for a in node.args]))
        if res.size > MAX_ARRAY_ELEMENTS:
            raise ValueError(f"Array too large (max {MAX_ARRAY_ELEMENTS} elements)")
        return res
    raise ValueError("Expression element not allowed in matrix mode")


def matrix_calculate(expr):
    if np is None:
        raise RuntimeError("NumPy is not installed (pip install numpy)")
    try:
        with np.errstate(all="raise"):
            res = mat_eval(parse_tree(expr), mat_funcs())
            if isinstance(res, tuple):
                raise ValueError("Result must be a number or a matrix")
            arr = np.asarray(res)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"NumPy evaluation error: {exc}") from exc
    if arr.ndim == 0:
        return format_number(arr.item())
    return np.array2string(arr, precision=10, suppress_small=False)


# ------------------------------ Heavy-task runner ----------------------------

_heavy_thread = None


def run_heavy(fn, timeout=None):
    """Run fn in a daemon thread with a wall-clock timeout (best effort:
    a runaway computation cannot be killed, but the server stays responsive
    and refuses further heavy work until the old one ends)."""
    global _heavy_thread
    timeout = HEAVY_TIMEOUT if timeout is None else timeout
    if _heavy_thread is not None and _heavy_thread.is_alive():
        raise RuntimeError("A previous symbolic/matrix computation is still running; restart the server")
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    th = threading.Thread(target=target, daemon=True)
    _heavy_thread = th
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise ValueError(f"Computation timed out after {timeout:g}s; simplify the expression")
    if "error" in box:
        raise box["error"]
    return box["value"]


# ------------------------------- Mode dispatcher -----------------------------

MATRIX_ONLY = {"det", "inv", "eigvals", "norm", "dot", "matmul", "zeros", "ones", "eye",
               "arange", "reshape", "transpose", "trace", "rank", "matpow", "array"}
SYMBOLIC_ONLY = {"solve", "diff", "integrate", "simplify", "factor", "expand", "limit",
                 "Eq", "Rational", "Abs"}


def detect_mode(expr):
    if split_equation(expr) is not None:
        return "symbolic"
    tree = parse_tree(expr)
    called, has_var, has_matrix_syntax = set(), False, False
    for n in ast.walk(tree):
        if isinstance(n, ast.List) or (isinstance(n, ast.BinOp) and isinstance(n.op, ast.MatMult)):
            has_matrix_syntax = True
        if isinstance(n, ast.Call):
            nm = safe_call_name(n.func)
            if nm:
                called.add(nm)
        if isinstance(n, ast.Name) and n.id in SYM_VARS:
            has_var = True
    # Variables (x, y, z, t) exist only in symbolic mode, so they win over list
    # syntax: solve([x+y-3, x-y-1], [x, y]) is a system of equations, while
    # solve([[2,0],[0,4]], [2,4]) has no variables and is a matrix solve.
    if has_var:
        return "symbolic"
    if has_matrix_syntax or called & MATRIX_ONLY:
        return "matrix"
    if called & SYMBOLIC_ONLY:
        return "symbolic"
    return "numeric"


def calculate(expr, mode="auto"):
    if mode is None:
        mode = "auto"
    if mode not in MODES:
        raise ValueError("mode must be auto, numeric, symbolic or matrix")
    if not isinstance(expr, str):
        raise ValueError("expr must be a string")
    expr = preprocess(expr).strip()
    if not expr or len(expr) > MAX_EXPR_LEN:
        raise ValueError("Expression is empty or too long")
    if mode == "auto":
        mode = detect_mode(expr)
    if mode == "numeric":
        return format_number(numeric_eval(parse_tree(expr)))
    if mode == "symbolic":
        return run_heavy(lambda: symbolic_calculate(expr))
    return run_heavy(lambda: matrix_calculate(expr))


# ----------------------------------- MCP layer -------------------------------

TOOL_DESCRIPTION = (
    "Calculator (mode=auto picks numeric/symbolic/matrix). Use ^ for powers. "
    "Numeric: + - * / // % **, sqrt sin cos tan log(natural) log10 exp floor ceil factorial gcd, pi e. "
    "Finance: rates are DECIMAL PER PERIOD (0.01 = 1% per period), never percents; "
    "for 12% annual with monthly payments use monthly(12). "
    "pmt(rate,nper,pv), fv(rate,nper,pmt,pv=0), npv(rate,cf0,cf1,...), irr(cf0,cf1,...) "
    "(returns per-period rate), fv_growing_rent(rate,nper,mortgage,rent,annual_growth). "
    "Symbolic (variables x,y,z,t): solve(x^2-4,x), diff, integrate, limit, simplify, factor, expand; "
    "'x^2=4' solves the equation. "
    "Matrix: [[1,2],[3,4]] @ [[1],[0]], det, inv, eigvals, solve, transpose, trace, rank, zeros, ones, eye, "
    "matpow. ** on matrices is elementwise."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "expr": {"type": "string", "description": "Expression, e.g. pmt(monthly(12), 120, 8000000)"},
        "mode": {"type": "string", "enum": list(MODES)},
    },
    "required": ["expr"],
    "additionalProperties": False,
}


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
    sys.stdout.flush()


def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(request):
    if not isinstance(request, dict):
        return rpc_error(None, -32600, "Invalid Request")
    method = request.get("method")
    if "id" not in request:  # notification: never answer
        return None
    mid = request["id"]
    params = request.get("params")
    params = params if isinstance(params, dict) else {}

    if method == "initialize":
        pv = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv if pv in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "calculate", "description": TOOL_DESCRIPTION, "inputSchema": TOOL_SCHEMA,
        }]}}
    if method == "tools/call":
        if params.get("name") != "calculate":
            return rpc_error(mid, -32602, "Unknown tool name")
        args = params.get("arguments")
        args = args if isinstance(args, dict) else {}
        try:
            text, is_error = calculate(args.get("expr"), args.get("mode", "auto")), False
        except Exception as exc:  # noqa: BLE001 - reported to the model as a tool error
            text, is_error = f"Calculation error: {exc}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": is_error,
        }}
    return rpc_error(mid, -32601, f"Method not found: {method}")


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            send(rpc_error(None, -32700, "Parse error"))
            continue
        try:
            response = handle(request)
        except Exception as exc:  # noqa: BLE001
            rid = request.get("id") if isinstance(request, dict) else None
            response = rpc_error(rid, -32603, "Internal error") if rid is not None else None
            sys.stderr.write(f"[{SERVER_NAME}] internal error: {exc}\n")
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()