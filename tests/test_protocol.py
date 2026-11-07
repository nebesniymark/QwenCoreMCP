"""Protocol regression and integration tests (consolidated)."""
from ._support import (
    Draft202012Validator,
    Path,
    audit_BASE,
    audit_c,
    audit_d,
    audit_FILES,
    audit_m,
    audit_rpc_handler,
    audit_rpc_request,
    json,
    math_engine,
    memory_engine,
    os,
    pytest,
    random,
    regression_d,
    regression_legacy,
    regression_modern,
    regression_SERVERS,
    release_BASE,
    release_c,
    release_MODULES,
    release_rpc,
    security_memory,
    subprocess,
    sys,
    time_engine,
)


@pytest.mark.parametrize('module,handle,name', release_MODULES)


def test_release_modern_discovery_and_tool(module, handle, name, release_memory):
    meta = {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                      'io.modelcontextprotocol/clientCapabilities': {}}}
    disc = handle(release_rpc('server/discover', params=meta))['result']
    assert disc['supportedVersions'] == ['2026-07-28']
    assert disc['_meta']['io.modelcontextprotocol/serverInfo']['name'] == module.SERVER_NAME
    listed = handle(release_rpc('tools/list',params=meta))['result']
    assert listed['tools'][0]['name'] == name
    assert '_meta' in listed
    assert handle(release_rpc('ping',params=meta))['error']['code'] == -32601

@pytest.mark.parametrize('module,handle,name', release_MODULES)
def test_release_legacy_latest_handshake(module, handle, name):
    assert handle(release_rpc('initialize',params={'protocolVersion':'2025-11-25'}))['result']['protocolVersion'] == '2025-11-25'
    assert handle(release_rpc('initialize',params={'protocolVersion':'2099-01-01'}))['result']['protocolVersion'] in module.SUPPORTED_PROTOCOLS
    assert handle(release_rpc('ping'))['result'] == {}
    assert handle({'jsonrpc':'2.0','method':'notifications/initialized'}) is None

@pytest.mark.parametrize('module,handle,name', release_MODULES)
@pytest.mark.parametrize('bad',[
    {'jsonrpc':'1.0','id':7,'method':'ping'},
    {'jsonrpc':'2.0','id':{'oops':1},'method':'ping'},
    {'jsonrpc':'2.0','id':True,'method':'ping'},
    {'jsonrpc':'2.0','id':7,'method':3},
    {'jsonrpc':'2.0','id':7},
    [],
])
def test_release_invalid_rpc(module, handle, name, bad):
    assert handle(bad)['error']['code'] == -32600

@pytest.mark.parametrize('module,handle,name', release_MODULES)
def test_release_modern_invalid_metadata(module,handle,name):
    param={'_meta':{'io.modelcontextprotocol/protocolVersion':'2100-01-01','io.modelcontextprotocol/clientCapabilities':{}}}
    assert handle(release_rpc('tools/list',params=param))['error']['code']==-32022

@pytest.mark.parametrize('module,handle,name', release_MODULES)
def test_release_stdio_multi_request(module,handle,name,tmp_path):
    env=os.environ.copy();env['AI_MEMORY_FILE']=str(tmp_path/'state.json')
    meta={'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}}
    reqs=[release_rpc('server/discover',1,meta),release_rpc('initialize',2,{'protocolVersion':'2024-11-05'}),
          release_rpc('tools/list',3), release_rpc('bad.method',4),release_rpc('ping',5)]
    result=subprocess.run([sys.executable,str(release_BASE/(module.__name__+'.py'))], check=False, input='\n'.join(map(json.dumps,reqs))+'\n',env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0, result.stderr
    lines=[json.loads(x) for x in result.stdout.splitlines()]
    assert len(lines)==len(reqs)
    assert lines[0]['result']['supportedVersions']==['2026-07-28']
    assert lines[1]['result']['protocolVersion']=='2024-11-05'
    assert lines[4]['result']=={}

@pytest.mark.parametrize('module,handle,name', release_MODULES)
def test_release_large_stdio_input(module,handle,name,tmp_path):
    env=os.environ.copy();env['AI_MEMORY_FILE']=str(tmp_path/'state.json')
    req='{"jsonrpc":"2.0","id":1,"method":"ping","padding":"' + 'a'*70000 + '"}'
    result=subprocess.run([sys.executable,str(release_BASE/(module.__name__+'.py'))],check=False, input=req+'\n'+json.dumps(release_rpc('ping',2))+'\n',env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0, result.stderr
    data=list(map(json.loads,result.stdout.splitlines()))
    assert len(data)==2 and data[0]['error']['code']==-32600 and data[1]['result']=={}

def test_release_memory_tool_error_for_near_duplicate(release_memory):
    release_c.run_tool({'action':'save','value':'A repeated note documenting some design work'})
    payload=release_rpc('tools/call',params={'name':'manage_memory','arguments':{'action':'save','value':'A repeated note documenting some design works'}})
    res=release_c.handle_rpc(payload)['result']
    assert res['isError'] is True
    assert json.loads(res['content'][0]['text'])['not_saved'] is True

@pytest.mark.parametrize('mod,name',[(audit_m,'calculate'),(audit_d,'datetime_calendar'),(audit_c,'manage_memory')])
def test_audit_mcp_handshake_and_schema(mod,name):
    request=audit_rpc_request('initialize',params={'protocolVersion':'2024-11-05','clientInfo':{'name':'audit','version':'0'},'capabilities':{}})
    result=audit_rpc_handler(mod,request)
    assert result['result']['protocolVersion']=='2024-11-05'
    assert result['result']['capabilities'].get('tools') == {}
    assert result['jsonrpc']=='2.0'
    lst=audit_rpc_handler(mod,audit_rpc_request('tools/list'))['result']['tools']
    assert len(lst)==1 and lst[0]['name']==name
    Draft202012Validator.check_schema(lst[0]['inputSchema'])
    assert audit_rpc_handler(mod,{'jsonrpc':'2.0','method':'notifications/initialized'}) is None
    assert audit_rpc_handler(mod,audit_rpc_request('method/nonexistent'))['error']['code']==-32601
    assert audit_rpc_handler(mod,audit_rpc_request('ping'))['result']=={}

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_stdio_multiplex(module,tmp_path):
    env=os.environ.copy(); env['AI_MEMORY_FILE']=str(tmp_path/'tmp_memory.json')
    commands=[audit_rpc_request('initialize',i,{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}) for i in [1]]
    commands.append({'jsonrpc':'2.0','method':'notifications/initialized'})
    commands.extend([audit_rpc_request('ping',i) for i in range(2,25)])
    commands.append(audit_rpc_request('tools/list',25))
    raw='\n'.join(json.dumps(x,ensure_ascii=False) for x in commands)+'\n'+'{bad json\n'
    p=subprocess.run([sys.executable,'-u',str(audit_BASE/(module+'.py'))], check=False, input=raw,capture_output=True,text=True,encoding='utf8',env=env,timeout=20)
    assert p.returncode==0,p.stderr
    responses=[json.loads(x) for x in p.stdout.splitlines()]
    assert len(responses)==26  # init + 23 ping + list + parse error
    assert [x['id'] for x in responses[:25]]==[1]+list(range(2,25))+[25]
    assert responses[-1]['error']['code']==-32700

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_stdio_malformed_fuzz(module,tmp_path):
    r=random.Random(786)
    invalid=['!', '[','{','"broken','{"a":','false,','[undefined]', '{"jsonrpc":2}']
    invalid += [''.join(r.choice('{}[],@#$012345abcxyz') for j in range(r.randrange(1,40))) for i in range(100)]
    input_data='\n'.join(invalid)+'\n'
    env=os.environ.copy();env['AI_MEMORY_FILE']=str(tmp_path/'memory.json')
    p=subprocess.run([sys.executable,str(audit_BASE/(module+'.py'))],check=False, input=input_data,capture_output=True,text=True,encoding='utf8',env=env,timeout=15)
    assert p.returncode==0,p.stderr
    for line in p.stdout.splitlines():
        reply=json.loads(line)
        assert reply.get('jsonrpc')=='2.0'
        assert 'error' in reply

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_expected_jsonrpc_version_validation(module):
    mod={'math_engine':audit_m,'time_engine':audit_d,'memory_engine':audit_c}[module]
    res=audit_rpc_handler(mod,{'jsonrpc':'1.0','id':42,'method':'ping'})
    assert res['error']['code']==-32600

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_expected_jsonrpc_id_validation(module):
    mod={'math_engine':audit_m,'time_engine':audit_d,'memory_engine':audit_c}[module]
    res=audit_rpc_handler(mod,{'jsonrpc':'2.0','id':{'oops':1},'method':'ping'})
    assert res['error']['code']==-32600

def test_audit_expected_memory_negotiate_supported_protocol():
    res=audit_c.handle_rpc(audit_rpc_request('initialize',params={'protocolVersion':'2099-01-01','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}))
    assert res['result']['protocolVersion'] in ['2024-11-05','2025-03-26','2025-06-18','2025-11-25']

@pytest.mark.parametrize('module,handle,tool_name', regression_SERVERS)
def test_regression_modern_discovery_result_required_fields(module, handle, tool_name, regression_memory):
    result = handle(regression_modern('server/discover'))['result']
    assert result['resultType'] == 'complete'
    assert result['supportedVersions'] == ['2026-07-28']
    assert result['capabilities'] == {'tools': {}}
    assert result['ttlMs'] == 0 and result['cacheScope'] == 'private'
    assert result['_meta']['io.modelcontextprotocol/serverInfo']['name'] == module.SERVER_NAME

def test_regression_datetime_initialize_explicit_null():
    result = regression_d.handle(regression_legacy('initialize',params=None))['result']
    assert result['protocolVersion'] in regression_d.SUPPORTED_PROTOCOLS

@pytest.mark.parametrize('module,handle,tool_name',regression_SERVERS)
def test_regression_invalid_json_string_returns_tool_error(module,handle,tool_name,regression_memory):
    result=handle(regression_legacy('tools/call',params={'name':tool_name,'arguments':'{"invalid'}))['result']
    assert result['isError'] is True

@pytest.mark.parametrize("key", [
    "token_limit", "token_budget", "token_count", "token_usage",
    "context_token_limit", "context_window_tokens", "max_tokens",
    "max_output_tokens", "prompt_tokens", "completion_tokens",
    "tokens_per_second", "TokenLimit", "maxCompletionTokens",
])
def test_security_token_metadata_is_allowed(security_memory_file, key):
    value = "4096 tokens"
    result = security_memory.run_tool({
        "action": "save", "category": "profile", "key": key, "value": value,
    })
    assert result["ok"]
    normalized = security_memory.re.sub(r"\W+", "_", security_memory.norm(key)).strip("_")
    assert security_memory.run_tool({"action": "read", "category": "profile"})["profile"][normalized] == value

@pytest.mark.parametrize("value", [
    "github_pat_" + "A" * 35,
    "glpat-" + "a" * 25,
    "-----BEGIN PRIVATE KEY-----",
])
def test_security_token_metadata_cannot_hide_structured_secret(security_memory_file, value):
    with pytest.raises(ValueError, match="secret"):
        security_memory.run_tool({
            "action": "save", "category": "profile", "key": "token_limit", "value": value,
        })
    assert not security_memory_file.exists()

@pytest.mark.parametrize("module,handler,tool_name", release_MODULES)
def test_prepush_handshake_fallback_latest_and_explicit_legacy(module, handler, tool_name):
    for requested in ("2099-01-01", "2026-07-28", None):
        params = {"protocolVersion": requested}
        response = handler({"jsonrpc": "2.0", "method": "initialize", "id": 123, "params": params})
        assert response["result"]["protocolVersion"] == module.SUPPORTED_PROTOCOLS[0]
    for requested in module.SUPPORTED_PROTOCOLS:
        response = handler({"jsonrpc": "2.0", "method": "initialize", "id": 123,
                            "params": {"protocolVersion": requested}})
        assert response["result"]["protocolVersion"] == requested
    for method in ("ping", "tools/list", "initialize"):
        invalid = handler({"jsonrpc": "2.0", "method": method, "id": None})
        assert invalid["error"]["code"] == -32600
    assert handler({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handler({"jsonrpc": "2.0", "method": "ping", "id": 1})["result"] == {}

@pytest.mark.parametrize("engine", [math_engine, time_engine, memory_engine])
def test_notification_with_bad_metadata_does_not_reply(engine):
    handler = getattr(engine, "handle_rpc", getattr(engine, "handle", None))
    assert handler({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {
        "_meta": {"io.modelcontextprotocol/protocolVersion": "unsupported"}}}) is None

@pytest.mark.parametrize("engine", [math_engine, time_engine, memory_engine])
def test_rejects_fractional_jsonrpc_id(engine):
    assert engine.rpc_validate({"jsonrpc": "2.0", "method": "ping", "id": 1.5})["error"]["code"] == -32600

@pytest.mark.parametrize("module", ["math_engine.py", "time_engine.py", "memory_engine.py"])
def test_transport_recovers_after_bad_json(module, tmp_path):
    """Invalid JSON or invalid metadata must not kill the subprocess."""
    env = {**os.environ, "AI_MEMORY_FILE": str(tmp_path / "memory.json")}
    commands = [
        b'{"jsonrpc":"2.0","method":"ping","id":' + b'9' * 5000 + b'}\n',
        b'[' * 1500 + b'0' + b']' * 1500 + b'\n',
        (b'{"jsonrpc":"2.0","method":"ping","id":9,"params":{"_meta":'
         b'{"io.modelcontextprotocol/protocolVersion":1e999}}}\n'),
        b'{"jsonrpc":"2.0","method":"ping","id":10}\n',
    ]
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / module)], input=b''.join(commands),
                            capture_output=True, env=env, timeout=25, check=False)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[-1]["result"] == {}
    assert responses[-1]["id"] == 10
    assert len(responses) == 4

@pytest.mark.parametrize("module", ["math_engine.py", "time_engine.py", "memory_engine.py"])
def test_transport_survives_repeated_corrupt_inputs(module: str, tmp_path: Path) -> None:
    """A burst of malformed requests cannot prevent a following valid ping."""
    malformed = [b"{\n", b"\xff\n", b"[" * 1200 + b"0" + b"]" * 1200 + b"\n"]
    requests = malformed * 8 + [b'{"jsonrpc":"2.0","method":"ping","id":123}\n']
    env = {**os.environ, "AI_MEMORY_FILE": str(tmp_path / "memory.json")}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / module)],
        input=b"".join(requests), capture_output=True, check=False,
        env=env, timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    responses = [json.loads(row) for row in result.stdout.splitlines()]
    assert len(responses) == len(requests)
    assert responses[-1] == {"jsonrpc": "2.0", "id": 123, "result": {}}
    assert all("error" in response for response in responses[:-1])
