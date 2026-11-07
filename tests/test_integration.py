"""Integration regression and integration tests (consolidated)."""
from ._support import (
    audit_BASE,
    audit_c,
    audit_d,
    audit_FILES,
    audit_m,
    audit_rpc_handler,
    audit_rpc_request,
    calc,
    calendar,
    json,
    math_engine,
    memory,
    memory_engine,
    pytest,
    qwen_rpc_tools,
    regression_legacy,
    regression_modern,
    regression_SERVERS,
    release_c,
    release_d,
    release_m,
    release_MODULES,
    security_memory,
)


@pytest.mark.parametrize('module,handle,name', release_MODULES)


def test_release_null_id_rejected(module, handle, name):
    # MCP's request schema uses non-null string/number identifiers.
    assert handle({'jsonrpc':'2.0','method':'ping','id':None})['error']['code'] == -32600

def test_release_integration_all(release_memory):
    release_c.run_tool({'action':'save','category':'profile','key':'principal','value':'8000000'})
    release_c.run_tool({'action':'save','category':'profile','key':'rate','value':'12'})
    profile=release_c.run_tool({'action':'read','category':'profile'})['profile']
    val=release_m.calculate(f'P={profile["principal"]}\nr=monthly({profile["rate"]})\npmt(r,120,P)')
    due=release_d.run_tool({'action':'add','datetime':'2024-01-31T12:00:00.123456Z','amount':1,'unit':'months'})['result']
    assert due.startswith('2024-02-29') and '.123456' in due
    release_c.run_tool({'action':'save','value':f'Payment {val}, due {due[:10]}'})
    assert val in release_c.run_tool({'action':'read','query':'Payment'})['notes'][0]['text']

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_compile(module):
    compile((audit_BASE/(module+'.py')).read_bytes(),str(audit_BASE/(module+'.py')),'exec')

@pytest.mark.parametrize('mod,name,args',[(audit_m,'calculate',{'expr':'2+2'}),(audit_d,'datetime_calendar',{'action':'weekday','date':'2000-01-01'}),(audit_c,'manage_memory',{'action':'read'})])
def test_audit_mcp_calls(mod,name,args,audit_memory):
    p=audit_rpc_request('tools/call',params={'name':name,'arguments':args})
    res=audit_rpc_handler(mod,p)['result']
    assert res['isError'] is False
    assert len(res['content'])==1 and res['content'][0]['type']=='text'
    assert audit_rpc_handler(mod,audit_rpc_request('tools/call',params={'name':'unknown'}))['error']['code']==-32602

@pytest.mark.parametrize('mod,name,args',[(audit_m,'calculate',{'expr':'1/0'}),(audit_d,'datetime_calendar',{'action':'weekday','date':'2023-02-29'}),(audit_c,'manage_memory',{'action':'save','category':'note','value':''})])
def test_audit_mcp_errors_keep_process(mod,name,args,audit_memory):
    res=audit_rpc_handler(mod,audit_rpc_request('tools/call',params={'name':name,'arguments':args}))['result']
    assert res['isError'] is True
    assert audit_rpc_handler(mod,audit_rpc_request('ping'))['result']=={}

def test_audit_integration_all_three(audit_memory):
    saved=audit_c.run_tool({'action':'save','category':'note','value':'Mortgage principal 8000000; annual rate 12%; periods 120; start 2024-01-31T12:00:00Z'})
    assert saved['saved']==1
    r=audit_c.run_tool({'action':'read','query':'mortgage'})
    assert r['notes']
    p=audit_m.calculate('pmt(monthly(12),120,8000000)')
    due=audit_d.run_tool({'action':'add','datetime':'2024-01-31T12:00:00Z','amount':1,'unit':'months'})['result']
    diff=audit_d.run_tool({'action':'difference','start':'2024-01-31T12:00:00Z','end':due})['days']
    assert diff==29
    audit_c.run_tool({'action':'save','id':1,'value':f'Monthly payment {p}, first due {due[:10]}'} )
    assert p in audit_c.run_tool({'action':'read','query':'payment'})['notes'][0]['text']

def test_qwen_matrices_and_local_variables():
    assert calc.calculate("det([[4,2],[3,5]])") == "14"
    assert calc.calculate("P=6000000; r=monthly(10); pmt(r,60,P)") == calc.calculate("pmt(monthly(10),60,6000000)")
    assert calc.calculate("[[4,2],[3,5]] @ [[1],[0]]") == "[[4.]\n [3.]]"

def test_qwen_same_three_tool_names_and_schemas():
    expected = [
        (calc.handle, "calculate", ["expr"]),
        (calendar.handle, "datetime_calendar", ["action"]),
        (memory.handle_rpc, "manage_memory", ["action"]),
    ]
    for handler, name, required in expected:
        tools = qwen_rpc_tools(handler)
        assert len(tools) == 1
        tool, = tools
        assert tool["name"] == name
        assert tool["inputSchema"]["required"] == required
        json.dumps(tools, ensure_ascii=False)  # JSON serializability

def test_qwen_expected_error_then_recover():
    def invoke(expr):
        return calc.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "calculate", "arguments": {"expr": expr}},
        })["result"]
    denied = invoke("2^1000000")
    assert denied["isError"] is True
    good = invoke("15*15")
    assert good["isError"] is False
    assert good["content"][0]["text"] == "225"

@pytest.mark.parametrize('module,handle,tool_name', regression_SERVERS)
def test_regression_modern_list_cache_schema(module, handle, tool_name, regression_memory):
    result = handle(regression_modern('tools/list'))['result']
    assert result['resultType'] == 'complete'
    assert result['ttlMs'] == 0 and result['cacheScope'] == 'private'
    assert len(result['tools']) == 1 and result['tools'][0]['name'] == tool_name
    legacy_res = handle(regression_legacy('tools/list'))['result']
    assert all(key not in legacy_res for key in ('resultType', 'ttlMs', 'cacheScope'))

@pytest.mark.parametrize('module,handle,tool_name', regression_SERVERS)
def test_regression_modern_call_has_result_type(module, handle, tool_name, regression_memory):
    args = ({'expr': '2+2'} if tool_name == 'calculate' else
            {'action': 'now'} if tool_name == 'datetime_calendar' else
            {'action': 'read'})
    result = handle(regression_modern('tools/call', args={'name': tool_name, 'arguments': args}))['result']
    assert result['resultType'] == 'complete'
    assert isinstance(result['content'], list)
    assert not result['isError']
    assert 'ttlMs' not in result and 'cacheScope' not in result

@pytest.mark.parametrize('module,handle,tool_name', regression_SERVERS)
def test_regression_version_error_has_structured_data(module, handle, tool_name, regression_memory):
    error = handle(regression_modern('tools/list', version='2099-01-01'))['error']
    assert error['code'] == -32022
    assert '2026-07-28' in error['data']['supported']
    assert '2025-11-25' in error['data']['supported']
    assert error['data']['requested'] == '2099-01-01'

@pytest.mark.parametrize('module,handle,tool_name',regression_SERVERS)
def test_regression_json_string_arguments_consistently_supported(module,handle,tool_name,regression_memory):
    args = ({'expr':'2+2'} if tool_name == 'calculate' else
            {'action':'now'} if tool_name == 'datetime_calendar' else
            {'action':'read'})
    result=handle(regression_legacy('tools/call',params={'name':tool_name,'arguments':json.dumps(args)}))['result']
    assert result['isError'] is False, result

@pytest.mark.parametrize("value", [
    "My password is not saved here",
    "Bearer token authentication is supported",
    "The Stripe product is not a payment key",
    "postgres://localhost:5432/mydb",
    "hf model identifier",
])
def test_prepush_harmless_prose_not_rejected(security_memory_file, value):
    assert security_memory.run_tool({"action": "save", "category": "note", "value": value})["ok"]

def test_solve_guard_rejects_before_numpy(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("np.linalg.solve called before result-size guard")
    monkeypatch.setattr(math_engine.np.linalg, "solve", forbidden)
    with pytest.raises(ValueError, match="exceed"):
        math_engine._solve_safe(math_engine.np.ones((128, 1, 1, 1)),
                                math_engine.np.ones((1, 128, 1, 1)))

def test_solve_normal_case():
    result = math_engine._solve_safe([[2., 0.], [0., 4.]], [6., 8.])
    assert result.tolist() == pytest.approx([3., 2.])

@pytest.mark.parametrize("args,pattern", [
    ({"action": "read", "id": 999}, "not found"),
    ({"action": "read", "id": 1, "category": "profile"}, "requires category"),
    ({"action": "read", "id": 1.5}, "whole number"),
])
def test_full_read_rejects_invalid_id(args: dict, pattern: str) -> None:
    state = {"profile": {}, "notes": [{"id": 1, "text": "hello", "project": None,
                                         "updated_at": "2026-11-07"}], "next_id": 2}
    with pytest.raises(ValueError, match=pattern):
        memory_engine.tool_read(args, state)
