"""Regression tests for release candidates. Run: pytest -q test_release.py"""
import importlib.util
import json
import math
import os
import pathlib
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

BASE = pathlib.Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

m = load('math_calculator')
d = load('datetime_mcp')
c = load('compact_memory_mcp')
MODULES = [(m, m.handle, 'calculate'), (d, d.handle, 'datetime_calendar'), (c, c.handle_rpc, 'manage_memory')]

@pytest.fixture
def memory(tmp_path, monkeypatch):
    f = tmp_path/'user.json'
    monkeypatch.setattr(c, 'MEMORY_FILE', f)
    monkeypatch.setattr(c, 'BACKUP_FILE', f.with_name('user.json.bak'))
    monkeypatch.setattr(c, 'LOCK_FILE', f.with_name('user.json.lock'))
    monkeypatch.setattr(c, 'LEGACY_FILE', tmp_path/'old.json')
    return f

def rpc(method, rid=1, params=None):
    return {'jsonrpc':'2.0','id':rid,'method':method,'params':params or {}}

@pytest.mark.parametrize('module,handle,name', MODULES)
def test_modern_discovery_and_tool(module, handle, name, memory):
    meta = {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                      'io.modelcontextprotocol/clientCapabilities': {}}}
    disc = handle(rpc('server/discover', params=meta))['result']
    assert disc['supportedVersions'] == ['2026-07-28']
    assert disc['_meta']['io.modelcontextprotocol/serverInfo']['name'] == module.SERVER_NAME
    listed = handle(rpc('tools/list',params=meta))['result']
    assert listed['tools'][0]['name'] == name
    assert '_meta' in listed
    assert handle(rpc('ping',params=meta))['error']['code'] == -32601

@pytest.mark.parametrize('module,handle,name', MODULES)
def test_legacy_latest_handshake(module, handle, name):
    assert handle(rpc('initialize',params={'protocolVersion':'2025-11-25'}))['result']['protocolVersion'] == '2025-11-25'
    assert handle(rpc('initialize',params={'protocolVersion':'2099-01-01'}))['result']['protocolVersion'] in module.SUPPORTED_PROTOCOLS
    assert handle(rpc('ping'))['result'] == {}
    assert handle({'jsonrpc':'2.0','method':'notifications/initialized'}) is None

@pytest.mark.parametrize('module,handle,name', MODULES)
@pytest.mark.parametrize('bad',[
    {'jsonrpc':'1.0','id':7,'method':'ping'},
    {'jsonrpc':'2.0','id':{'oops':1},'method':'ping'},
    {'jsonrpc':'2.0','id':True,'method':'ping'},
    {'jsonrpc':'2.0','id':7,'method':3},
    {'jsonrpc':'2.0','id':7},
    [],
])
def test_invalid_rpc(module, handle, name, bad):
    assert handle(bad)['error']['code'] == -32600

@pytest.mark.parametrize('module,handle,name', MODULES)
def test_null_id_valid(module, handle, name):
    assert handle({'jsonrpc':'2.0','method':'ping','id':None})['result'] == {}

@pytest.mark.parametrize('module,handle,name', MODULES)
def test_modern_invalid_metadata(module,handle,name):
    param={'_meta':{'io.modelcontextprotocol/protocolVersion':'2100-01-01','io.modelcontextprotocol/clientCapabilities':{}}}
    assert handle(rpc('tools/list',params=param))['error']['code']==-32022

@pytest.mark.parametrize('module,handle,name', MODULES)
def test_stdio_multi_request(module,handle,name,tmp_path):
    env=os.environ.copy();env['AI_MEMORY_FILE']=str(tmp_path/'state.json')
    meta={'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}}
    reqs=[rpc('server/discover',1,meta),rpc('initialize',2,{'protocolVersion':'2024-11-05'}),
          rpc('tools/list',3), rpc('bad.method',4),rpc('ping',5)]
    result=subprocess.run([sys.executable,str(BASE/(module.__name__+'.py'))], input='\n'.join(map(json.dumps,reqs))+'\n',env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0, result.stderr
    lines=[json.loads(x) for x in result.stdout.splitlines()]
    assert len(lines)==len(reqs)
    assert lines[0]['result']['supportedVersions']==['2026-07-28']
    assert lines[1]['result']['protocolVersion']=='2024-11-05'
    assert lines[4]['result']=={}

@pytest.mark.parametrize('module,handle,name', MODULES)
def test_large_stdio_input(module,handle,name,tmp_path):
    env=os.environ.copy();env['AI_MEMORY_FILE']=str(tmp_path/'state.json')
    req='{"jsonrpc":"2.0","id":1,"method":"ping","padding":"' + 'a'*70000 + '"}'
    result=subprocess.run([sys.executable,str(BASE/(module.__name__+'.py'))],input=req+'\n'+json.dumps(rpc('ping',2))+'\n',env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0, result.stderr
    data=list(map(json.loads,result.stdout.splitlines()))
    assert len(data)==2 and data[0]['error']['code']==-32600 and data[1]['result']=={}

@pytest.mark.parametrize('expr,expected',[
    ('P=8000000\nr=0.01\nP*r','80000'),
    ('P=100; r=0.01; P*r','1'),
    ('a=2\nb=a^3\nb','8'),
    ('monthly_rate=monthly(12)\nprincipal=8000000\npmt(monthly_rate,120,principal)', '114776.758'),
])
def test_math_assignments(expr,expected):
    res=m.calculate(expr)
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
def test_math_assignments_safe(expr):
    with pytest.raises(ValueError): m.calculate(expr)

def test_math_assignment_scope():
    assert m.calculate('x=2\nx+1')=='3'
    with pytest.raises(ValueError): m.calculate('x+1',mode='numeric')

def test_math_pmt_near_zero():
    assert math.isclose(float(m.calculate('pmt(1e-17,120,1000)')),1000/120,rel_tol=1e-13)

@pytest.mark.parametrize('months',['0.5','2.5','99.3'])
def test_math_fractional_periods_rejected(months):
    with pytest.raises(ValueError,match='whole number'):
        m.calculate('fv_growing_rent(0.01,'+months+',100,40,0.06)')

@pytest.mark.parametrize('expr',['array([1e309])','array([1,1e309])','arange(0,1e309)','ones([100000,2])'])
def test_math_reject_nonfinite_huge_matrix(expr):
    with pytest.raises(ValueError):m.calculate(expr)

def test_math_worker_timeout_recovery():
    with pytest.raises(ValueError, match='timed out'):
        m.run_heavy(m.preprocess('expand((x+1)^500)'),'symbolic',timeout=0.0001)
    assert m.calculate('det([[1,2],[3,4]])') == '-2'

def test_math_output_budget():
    text=m.calculate('expand((x+1)^250)')
    assert text.startswith('[TRUNCATED') and len(text) < 3650
    arr=m.calculate('ones([100,100])')
    assert 'TRUNCATED' in arr and len(arr)<3700

@pytest.mark.parametrize('action',[
    {'action':'add','datetime':'2026-01-01T00:00:00.123456Z','unit':'seconds','amount':1},
    {'action':'add','datetime':'2026-01-01T00:00:00.123456Z','unit':'months','amount':1},
])
def test_datetime_microseconds(action):
    assert '.123456' in d.run_tool(action)['result']

@pytest.mark.parametrize('bad',['2026-W05-3','2026-1-1','20260101'])
def test_date_strict(bad):
    with pytest.raises(ValueError): d.run_tool({'action':'weekday','date':bad})

def test_datetime_utc_without_tzdata(tmp_path):
    env=os.environ.copy();env['PYTHONTZPATH']=str(tmp_path/'missing')
    payload=rpc('tools/call',params={'name':'datetime_calendar','arguments':{'action':'now'}})
    p=subprocess.run([sys.executable,'-S',str(BASE/'datetime_mcp.py')],input=json.dumps(payload)+'\n',text=True,capture_output=True,env=env,timeout=10)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['result']['isError'] is False

@pytest.mark.parametrize('zone,utc,expected',[
    ('America/New_York','2024-03-10T07:30:00Z','2024-03-10T03:30:00-04:00'),
    ('Europe/Berlin','2024-03-31T01:30:00Z','2024-03-31T03:30:00+02:00'),
    ('Australia/Sydney','2024-10-05T16:30:00Z','2024-10-06T03:30:00+11:00'),
])
def test_datetime_iana_conversion(zone,utc,expected):
    assert d.run_tool({'action':'convert','datetime':utc,'timezone':zone})['result']==expected

def test_datetime_localize_overlap_and_gap():
    for fold,ts in [(0,1730611800),(1,1730615400)]:
        result=d.run_tool({'action':'localize','datetime':'2024-11-03T01:30:00','timezone':'America/New_York','fold':fold})
        assert result['unix_timestamp']==ts
    with pytest.raises(ValueError,match='Nonexistent'):
        d.run_tool({'action':'localize','datetime':'2024-03-10T02:30:00','timezone':'America/New_York'})

def test_datetime_timestamp_units():
    assert d.run_tool({'action':'timestamp','timestamp':-1000,'timestamp_unit':'milliseconds'})['result']=='1969-12-31T23:59:59+00:00'
    assert d.run_tool({'action':'timestamp','timestamp':0})['result']=='1970-01-01T00:00:00+00:00'

def test_memory_secret_fields(memory):
    for args in [
        {'action':'save','value':'legit','project':'sk-ABCDEF1234567890ABCDEF1234567890'},
        {'action':'save','category':'profile','key':'password:AB12345678','value':'value'},
        {'action':'save','category':'profile','key':'valid','value':'password:AB12345678'},
    ]:
        with pytest.raises(ValueError): c.run_tool(args)
    assert not memory.exists()

def test_memory_permissions(memory):
    c.run_tool({'action':'save','value':'first'})
    c.run_tool({'action':'save','value':'second'})
    if os.name!='nt':
        for file in [memory,c.BACKUP_FILE,c.LOCK_FILE,*memory.parent.glob('*.bak')]:
            assert stat.S_IMODE(file.stat().st_mode)==0o600,file

def test_memory_legacy_and_conflict_detection():
    assert c._clean({'next_id':1,'notes':[{'id':'n_7','text':'first'},{'id':8,'text':'second'}]})['next_id']==9
    with pytest.raises(ValueError,match='duplicate'):
        c._clean({'notes':[{'id':'n_7','text':'x'},{'id':7,'text':'y'}]})

def test_memory_invalid_json_structure_not_discarded(memory):
    memory.write_text(json.dumps({'profile':{},'notes':'corrupted'}),encoding='utf8')
    with pytest.raises(ValueError):c.load_memory()
    assert list(memory.parent.glob('user.json.corrupt-*'))
    with pytest.raises(ValueError):c.run_tool({'action':'read'})

def test_memory_backup_recovery(memory):
    c.run_tool({'action':'save','value':'A'})
    c.run_tool({'action':'save','value':'B'})
    memory.write_text('{ broken JSON',encoding='utf8')
    res=c.run_tool({'action':'read'})
    assert len(res['notes'])==1 and res['notes'][0]['text']=='A'
    assert list(memory.parent.glob('user.json.corrupt-*'))

def test_memory_number_dedup_and_projects(memory):
    assert c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150000 RUB','project':'work'})['saved']==1
    assert c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150001 RUB','project':'work'})['saved']==2
    assert c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150000 RUB','project':'other'})['saved']==3

def test_memory_tool_error_for_near_duplicate(memory):
    c.run_tool({'action':'save','value':'A repeated note documenting some design work'})
    payload=rpc('tools/call',params={'name':'manage_memory','arguments':{'action':'save','value':'A repeated note documenting some design works'}})
    res=c.handle_rpc(payload)['result']
    assert res['isError'] is True
    assert json.loads(res['content'][0]['text'])['not_saved'] is True

def test_memory_reject_long_project(memory):
    with pytest.raises(ValueError):c.run_tool({'action':'save','value':'abc','project':'a'*61})

def test_memory_secret_redaction_existing(memory):
    memory.write_text(json.dumps({'profile':{'private':'password:ABCDEFG12345'},'notes':[]}),encoding='utf8')
    assert c.run_tool({'action':'read','category':'profile'})['profile']['private'].startswith('[REDACTED')

def test_memory_symlink_refused(memory,tmp_path):
    target=tmp_path/'victim';target.write_text('do-not-change')
    try:memory.symlink_to(target)
    except (OSError,NotImplementedError):pytest.skip('no symlink support')
    with pytest.raises(OSError):c.run_tool({'action':'save','value':'hello'})
    assert target.read_text()=='do-not-change'

def test_memory_multithread_process_isolation(tmp_path):
    path=tmp_path/'db.json'; env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def writer(k):
        requests=[rpc('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','value':f'v{k}_{i}'}}) for i in range(12)]
        p=subprocess.run([sys.executable,str(BASE/'compact_memory_mcp.py')],input='\n'.join(json.dumps(x) for x in requests)+'\n',text=True,capture_output=True,env=env,timeout=20)
        assert p.returncode==0,p.stderr
        assert all(x['result']['isError'] is False for x in map(json.loads,p.stdout.splitlines()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer,range(4)))
    entries=json.loads(path.read_text())['notes']
    assert len(entries)==48 and len({n['id'] for n in entries})==48

def test_integration_all(memory):
    c.run_tool({'action':'save','category':'profile','key':'principal','value':'8000000'})
    c.run_tool({'action':'save','category':'profile','key':'rate','value':'12'})
    profile=c.run_tool({'action':'read','category':'profile'})['profile']
    val=m.calculate(f'P={profile["principal"]}\nr=monthly({profile["rate"]})\npmt(r,120,P)')
    due=d.run_tool({'action':'add','datetime':'2024-01-31T12:00:00.123456Z','amount':1,'unit':'months'})['result']
    assert due.startswith('2024-02-29') and '.123456' in due
    c.run_tool({'action':'save','value':f'Payment {val}, due {due[:10]}'})
    assert val in c.run_tool({'action':'read','query':'Payment'})['notes'][0]['text']

def test_memory_old_hex_ids():
    assert c._clean({'notes':[{'id':'n_a1b2c3','text':'old memo'}]})['notes'][0]['id']==0xa1b2c3
    assert c.arg_id({'id':'n_a1b2c3'})==0xa1b2c3

def test_matrix_broadcast_bomb_rejected():
    for expr in ['ones([10000,1])+ones([1,10000])',
                 'matmul(ones([10000,1]),ones([1,10000]))',
                 'dot(ones([10000,1]),ones([1,10000]))']:
        with pytest.raises(ValueError,match='exceed'):
            m.calculate(expr)

def test_datetime_iana_wall_vs_elapsed_dst():
    base={'action':'add','datetime':'2024-03-09T12:00:00-05:00',
          'timezone':'America/New_York'}
    assert d.run_tool(dict(base,unit='days',amount=1))['result']=='2024-03-10T12:00:00-04:00'
    assert d.run_tool(dict(base,unit='hours',amount=24))['result']=='2024-03-10T13:00:00-04:00'

def test_memory_unknown_nonempty_json_rejected():
    with pytest.raises(ValueError,match='missing profile'):
        c._clean({'injected':'nonsense'})
