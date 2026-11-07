"""Math regression and integration tests (consolidated)."""
from ._support import (
    Decimal,
    ModuleType,
    audit_m,
    calc,
    calculator,
    getcontext,
    math,
    math_engine,
    np,
    pytest,
    qwen_rpc_tools,
    random,
    regression_m,
    release_m,
)


@pytest.mark.parametrize('expr,expected',[
    ('P=8000000\nr=0.01\nP*r','80000'),
    ('P=100; r=0.01; P*r','1'),
    ('a=2\nb=a^3\nb','8'),
    ('monthly_rate=monthly(12)\nprincipal=8000000\npmt(monthly_rate,120,principal)', '114776.758'),
])


def test_release_math_assignments(expr,expected):
    res=release_m.calculate(expr)
    assert res.startswith(expected)

@pytest.mark.parametrize('expr',[
    'a=3\n__import__("os")',
    'a=3\na.__class__',
    'a=3\nopen("x")',
    'a=3\nfor i in range(2): pass\na',
    'a=3\na+=1\na',
    'a=3\n[a for a in [1]]',
    'a=3\nb=2',
])
def test_release_math_assignments_safe(expr):
    with pytest.raises(ValueError): release_m.calculate(expr)

def test_release_math_assignment_scope():
    assert release_m.calculate('x=2\nx+1')=='3'
    with pytest.raises(ValueError): release_m.calculate('x+1',mode='numeric')

def test_release_math_pmt_near_zero():
    assert math.isclose(float(release_m.calculate('pmt(1e-17,120,1000)')),1000/120,rel_tol=1e-13)

@pytest.mark.parametrize('months',['0.5','2.5','99.3'])
def test_release_math_fractional_periods_rejected(months):
    with pytest.raises(ValueError,match='whole number'):
        release_m.calculate('fv_growing_rent(0.01,'+months+',100,40,0.06)')

@pytest.mark.parametrize('expr',['array([1e309])','array([1,1e309])','arange(0,1e309)','ones([100000,2])'])
def test_release_math_reject_nonfinite_huge_matrix(expr):
    with pytest.raises(ValueError):release_m.calculate(expr)

def test_release_math_output_budget():
    text=release_m.calculate('expand((x+1)^250)')
    assert text.startswith('[TRUNCATED') and len(text) < 3650
    arr=release_m.calculate('ones([100,100])')
    assert 'TRUNCATED' in arr and len(arr)<3700

@pytest.mark.parametrize('expr,out',[('2+2*3','8'),('2^10','1024'),('-3^2','-9'),('(-3)^2','9'),('0.1+0.2','0.3'),('sqrt(16)','4'),('factorial(10)','3628800'),('gcd(24,36)','12'),('log(e)','1'),('pi','3.14159265358979'),('2**3**2','512'),('sin(0)','0'),('round(2.5)','2'),('pmt(0,10,100)','10'),('monthly(12)','0.01'),('pct(5)','0.05'),('npv(0,100,50,-20)','130')])
def test_audit_math_examples(expr,out): assert audit_m.calculate(expr)==out

@pytest.mark.parametrize('expr', ['1/0','2**600','2**5000','factorial(1001)','0**-1','__import__("os")','open("/etc/passwd")','(1).__class__','math.__dict__','[x for x in range(9)]','(lambda:4)()','[1,2]','sum([1,2])','9e999','2+nan','3 and 2', '10+'*1000])
def test_audit_math_forbidden(expr):
    with pytest.raises((ValueError, TypeError, OverflowError, ZeroDivisionError)): audit_m.calculate(expr,'numeric')

@pytest.mark.parametrize('expr', ['diff(x^3,x)','integrate(x,x)','solve(x^2-4,x)','x^2=4','factor(x^2-1)','simplify((x+1)^2-x^2-2*x-1)'])
def test_audit_math_symbolic(expr):
    out=audit_m.calculate(expr)
    assert out and not out.startswith('Calculation error')

@pytest.mark.parametrize('expr,expected', [('det([[1,2],[3,4]])','-2'),('trace([[1,2],[3,4]])','5'),('matmul([[1,0],[0,2]],[[2],[4]])','[[2.]\n [8.]]'),('eye(2)','[[1. 0.]\n [0. 1.]]'),('rank([[1,2],[2,4]])','1'),('dot([1,2],[3,4])','11')])
def test_audit_math_matrix(expr,expected): assert audit_m.calculate(expr)==expected

@pytest.mark.parametrize('expr,mode', [('2+1','numeric'),('diff(x,x)','symbolic'),('det([[2]])','matrix'),('solve([[2]],[4])','matrix'),('solve(x-2,x)','symbolic'),('x+1','symbolic'),('[[1,2]]','matrix')])
def test_audit_math_auto(expr,mode): assert audit_m.detect_mode(audit_m.preprocess(expr))==mode

def test_audit_math_randomized_integer_properties():
    r=random.Random(0xC0FFEE)
    for _ in range(500):
        a,b,c0=(r.randint(-100000,100000) for i in range(3))
        assert audit_m.calculate(f'({a}+{b})*{c0}')==str((a+b)*c0)
        assert audit_m.calculate(f'{a}*({b}+{c0})')==str(a*(b+c0))

def test_audit_math_randomized_finance_against_decimal():
    getcontext().prec=50
    r=random.Random(771)
    for _ in range(100):
        rate=Decimal(r.randint(1,250))/Decimal(10000)
        periods=r.randint(1,360)
        pv=r.randint(100,10000000)
        want=Decimal(pv)*rate/(1-(1+rate)**(-periods))
        got=float(audit_m.calculate(f'pmt({float(rate)},{periods},{pv})'))
        assert math.isclose(got,float(want),rel_tol=1e-11,abs_tol=1e-6)

def test_audit_math_randomized_matrix_against_numpy():
    r=random.Random(781)
    for _ in range(75):
        arr1=np.array([[r.randint(-4,4) for i in range(2)] for j in range(2)])
        arr2=np.array([[r.randint(-4,4) for i in range(2)] for j in range(2)])
        actual=audit_m.calculate(f'matmul({arr1.tolist()},{arr2.tolist()})')
        assert np.allclose(np.fromstring(actual.replace('[',' ').replace(']',' '),sep=' ').reshape(2,2),arr1@arr2)

def test_audit_expected_math_multistatement_assignments():
    assert audit_m.calculate('P=8000000\nr=0.01\nP*r')=='80000'

def test_audit_expected_math_pmt_near_zero():
    assert math.isclose(float(audit_m.calculate('pmt(1e-17,120,1000)')),1000/120,rel_tol=1e-12)

def test_audit_expected_math_noninteger_periods_rejected():
    with pytest.raises(ValueError): audit_m.calculate('fv_growing_rent(0.01,2.5,100,40,0.06)')

def test_audit_expected_matrix_nonfinite_rejected():
    with pytest.raises(ValueError):audit_m.calculate('array([1e309])')

def test_audit_math_heavy_timeout_liveness():
    with pytest.raises(ValueError,match='timed out'):
        audit_m.run_heavy(audit_m.preprocess('expand((x+1)^500)'), 'symbolic', timeout=.0001)
    assert audit_m.calculate('det([[1]])') == '1'

def test_qwen_math_descriptions_clarify_annual_percentage():
    tool, = qwen_rpc_tools(calc.handle)
    assert tool["name"] == "calculate"
    desc = tool["description"]
    assert "pmt(monthly(10),60,6000000)" in desc
    assert "do NOT use monthly(10/12)" in desc
    assert "decimals PER PERIOD" in desc
    assert "det([[4,2],[3,5]])=14" in desc
    assert "@ means matrix multiplication" in desc
    assert len(desc) < 800  # keep descriptions compact for a 4B model
    assert tool["inputSchema"]["required"] == ["expr"]

def test_qwen_interest_rates_and_mortgage_are_mathematically_correct():
    monthly = float(calc.calculate("monthly(10)"))
    assert math.isclose(monthly, 0.10 / 12, rel_tol=1e-13)
    good = float(calc.calculate("pmt(monthly(10),60,6000000)"))
    wrong = float(calc.calculate("pmt(monthly(10/12),60,6000000)"))
    assert math.isclose(good, 127482.26826761, rel_tol=1e-12)
    assert math.isclose(wrong, 102132.513691453, rel_tol=1e-12)
    assert good > wrong  # server evaluates inputs; model must choose right one

def test_regression_math_factorial_536_boundary():
    value = int(regression_m.calculate('factorial(536)'))
    assert value.bit_length() <= regression_m.MAX_INT_BITS
    with pytest.raises(ValueError,match='536'):
        regression_m.calculate('factorial(537)')

def test_regression_math_matrix_negative_literals_separate_ast_limit():
    expr = 'det(['+','.join('['+','.join(str(-int(i==j)) for j in range(12))+']' for i in range(12))+'])'
    assert regression_m.calculate(expr) in ('1','-1')
    assert regression_m.calculate(expr,mode='matrix') in ('1','-1')
    # Scalar mode still enforces stricter complexity, not simply accepting any AST.
    with pytest.raises(ValueError,match='complex'):
        regression_m.calculate('['+','.join('0' for _ in range(190))+']',mode='numeric')

def test_stable_small_rate_fv():
    assert math_engine.fv(1e-17, 120, 1) == pytest.approx(120, abs=1e-11)
    assert math_engine.fv(1e-7, 2, 10) == pytest.approx(20.000001, abs=1e-10)

def test_fv_zero_rate():
    assert math_engine.fv(0, 12, 100, pv=100) == 1300

def test_large_negative_rate_pmt_keeps_nonzero_result():
    assert math_engine.pmt(-0.99, 160, 1e300) == pytest.approx(9.9e-21, rel=1e-9)

@pytest.mark.parametrize("multiplier", [1.0, 1e-200, 1e-150, 1e150, 1e200])
def test_irr_is_invariant_to_cashflow_scale(multiplier: float) -> None:
    """Rescaling a two-payment loan does not change its IRR."""
    result = math_engine.irr(-multiplier, 1.111 * multiplier)
    assert result == pytest.approx(0.111, abs=1e-12)

def test_irr_finds_nearby_roots_in_one_old_grid_cell() -> None:
    assert math_engine.irr(-1, 3.703, -4.517802, 1.819953) == pytest.approx(0.101, abs=1e-9)

def test_irr_handles_even_multiplicity_root() -> None:
    assert math_engine.irr(-1, 3, -3, 1) == pytest.approx(0, abs=1e-9)

def test_irr_rejects_missing_sign_change() -> None:
    with pytest.raises(ValueError, match="negative and one positive"):
        math_engine.irr(1, 2, 3)

@pytest.mark.parametrize("cashflows,exception", [
    ((1,), ValueError),
    ((True, -1), TypeError),
    (("1", -1), TypeError),
    ((math.inf, -1), ValueError),
    ((math.nan, -1), ValueError),
])
def test_irr_rejects_invalid_cashflows(cashflows: tuple, exception: type[Exception]) -> None:
    with pytest.raises(exception):
        math_engine.irr(*cashflows)

@pytest.mark.parametrize("epsilon", [1e-10, 1e-12, 1e-13, 1e-14, 1e-15])
def test_irr_rejects_false_tangent_root(epsilon: float) -> None:
    """A near miss at x=1 must not outrank the real root at x=2."""
    # P(x) = ((x - 1)^2 + epsilon) * (x - 2).
    # There is no root near x=1 for positive epsilon.
    actual = math_engine.irr(-2 - 2 * epsilon, 5 + epsilon, -4, 1)
    assert actual == pytest.approx(-0.5, abs=1e-11)

@pytest.mark.parametrize("cashflows,expected", [
    ((-1, 3, -3, 1), 0.0),
    ((-4, 8, -5, 1), 0.0),  # (x-1)(x-2)^2, nearest is x=1
])
def test_irr_retains_exact_tangent_roots(cashflows: tuple[float, ...], expected: float) -> None:
    """Genuine multiple roots remain recognized after near-root rejection."""
    assert math_engine.irr(*cashflows) == pytest.approx(expected, abs=1e-10)

def test_irr_generated_two_cashflow_cases() -> None:
    """Deterministic property check against the analytic one-period solution."""
    import random

    rng = random.Random(20261107)
    for _ in range(250):
        rate = rng.uniform(-0.9, 9.0)
        amount = 10 ** rng.uniform(-200, 200)
        actual = math_engine.irr(-amount, amount * (1 + rate))
        assert actual == pytest.approx(rate, rel=1e-10, abs=1e-10)

def test_irr_generated_distinct_roots() -> None:
    """Find the closest-to-zero rate from two known real roots."""
    import random

    rng = random.Random(20261108)
    for _ in range(150):
        x1 = rng.uniform(0.15, 0.8)
        x2 = rng.uniform(1.4, 5.0)
        # (x-x1)(x-x2) with ascending cashflow coefficients.
        cashflows = (x1 * x2, -(x1 + x2), 1.0)
        expected = min((1 / x1 - 1, 1 / x2 - 1), key=abs)
        assert math_engine.irr(*cashflows) == pytest.approx(expected, abs=1e-10)

def test_optional_math_backends_are_modules_when_installed() -> None:
    """Optional backend aliases must keep the same public references."""
    assert calculator.sp is None or isinstance(calculator.sp, ModuleType)
    assert calculator.np is None or isinstance(calculator.np, ModuleType)
    assert calculator.calculate("2+2") == "4"
