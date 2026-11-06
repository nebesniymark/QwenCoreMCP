"""Unified QwenCoreMCP test suite: 278 legacy checks + focused security regressions.

Run with: python -m pytest -q
The four original suites are included below, separated by labeled sections.
"""



# ================================================================
# Original suite: test_release.py (release)
# ================================================================
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

release_BASE = pathlib.Path(__file__).resolve().parent

def release_load(name):
    spec = importlib.util.spec_from_file_location(name, release_BASE / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

release_m = release_load('math_calculator')
release_d = release_load('datetime_mcp')
release_c = release_load('compact_memory_mcp')
release_MODULES = [(release_m, release_m.handle, 'calculate'), (release_d, release_d.handle, 'datetime_calendar'), (release_c, release_c.handle_rpc, 'manage_memory')]

@pytest.fixture
def release_memory(tmp_path, monkeypatch):
    f = tmp_path/'user.json'
    monkeypatch.setattr(release_c, 'MEMORY_FILE', f)
    monkeypatch.setattr(release_c, 'BACKUP_FILE', f.with_name('user.json.bak'))
    monkeypatch.setattr(release_c, 'LOCK_FILE', f.with_name('user.json.lock'))
    monkeypatch.setattr(release_c, 'LEGACY_FILE', tmp_path/'old.json')
    return f

def release_rpc(method, rid=1, params=None):
    return {'jsonrpc':'2.0','id':rid,'method':method,'params':params or {}}

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
def test_release_null_id_rejected(module, handle, name):
    # MCP's request schema uses non-null string/number identifiers.
    assert handle({'jsonrpc':'2.0','method':'ping','id':None})['error']['code'] == -32600

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
    result=subprocess.run([sys.executable,str(release_BASE/(module.__name__+'.py'))], input='\n'.join(map(json.dumps,reqs))+'\n',env=env,text=True,capture_output=True,timeout=10)
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
    result=subprocess.run([sys.executable,str(release_BASE/(module.__name__+'.py'))],input=req+'\n'+json.dumps(release_rpc('ping',2))+'\n',env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0, result.stderr
    data=list(map(json.loads,result.stdout.splitlines()))
    assert len(data)==2 and data[0]['error']['code']==-32600 and data[1]['result']=={}

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

def test_release_math_worker_timeout_recovery():
    with pytest.raises(ValueError, match='timed out'):
        release_m.run_heavy(release_m.preprocess('expand((x+1)^500)'),'symbolic',timeout=0.0001)
    assert release_m.calculate('det([[1,2],[3,4]])') == '-2'

def test_release_math_output_budget():
    text=release_m.calculate('expand((x+1)^250)')
    assert text.startswith('[TRUNCATED') and len(text) < 3650
    arr=release_m.calculate('ones([100,100])')
    assert 'TRUNCATED' in arr and len(arr)<3700

@pytest.mark.parametrize('action',[
    {'action':'add','datetime':'2026-01-01T00:00:00.123456Z','unit':'seconds','amount':1},
    {'action':'add','datetime':'2026-01-01T00:00:00.123456Z','unit':'months','amount':1},
])
def test_release_datetime_microseconds(action):
    assert '.123456' in release_d.run_tool(action)['result']

@pytest.mark.parametrize('bad',['2026-W05-3','2026-1-1','20260101'])
def test_release_date_strict(bad):
    with pytest.raises(ValueError): release_d.run_tool({'action':'weekday','date':bad})

def test_release_datetime_utc_without_tzdata(tmp_path):
    env=os.environ.copy();env['PYTHONTZPATH']=str(tmp_path/'missing')
    payload=release_rpc('tools/call',params={'name':'datetime_calendar','arguments':{'action':'now'}})
    p=subprocess.run([sys.executable,'-S',str(release_BASE/'datetime_mcp.py')],input=json.dumps(payload)+'\n',text=True,capture_output=True,env=env,timeout=10)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['result']['isError'] is False

@pytest.mark.parametrize('zone,utc,expected',[
    ('America/New_York','2024-03-10T07:30:00Z','2024-03-10T03:30:00-04:00'),
    ('Europe/Berlin','2024-03-31T01:30:00Z','2024-03-31T03:30:00+02:00'),
    ('Australia/Sydney','2024-10-05T16:30:00Z','2024-10-06T03:30:00+11:00'),
])
def test_release_datetime_iana_conversion(zone,utc,expected):
    assert release_d.run_tool({'action':'convert','datetime':utc,'timezone':zone})['result']==expected

def test_release_datetime_localize_overlap_and_gap():
    for fold,ts in [(0,1730611800),(1,1730615400)]:
        result=release_d.run_tool({'action':'localize','datetime':'2024-11-03T01:30:00','timezone':'America/New_York','fold':fold})
        assert result['unix_timestamp']==ts
    with pytest.raises(ValueError,match='Nonexistent'):
        release_d.run_tool({'action':'localize','datetime':'2024-03-10T02:30:00','timezone':'America/New_York'})

def test_release_datetime_timestamp_units():
    assert release_d.run_tool({'action':'timestamp','timestamp':-1000,'timestamp_unit':'milliseconds'})['result']=='1969-12-31T23:59:59+00:00'
    assert release_d.run_tool({'action':'timestamp','timestamp':0})['result']=='1970-01-01T00:00:00+00:00'

def test_release_memory_secret_fields(release_memory):
    for args in [
        {'action':'save','value':'legit','project':'sk-ABCDEF1234567890ABCDEF1234567890'},
        {'action':'save','category':'profile','key':'password:AB12345678','value':'value'},
        {'action':'save','category':'profile','key':'valid','value':'password:AB12345678'},
    ]:
        with pytest.raises(ValueError): release_c.run_tool(args)
    assert not release_memory.exists()

def test_release_memory_permissions(release_memory):
    release_c.run_tool({'action':'save','value':'first'})
    release_c.run_tool({'action':'save','value':'second'})
    if os.name!='nt':
        for file in [release_memory,release_c.BACKUP_FILE,release_c.LOCK_FILE,*release_memory.parent.glob('*.bak')]:
            assert stat.S_IMODE(file.stat().st_mode)==0o600,file

def test_release_memory_legacy_and_conflict_detection():
    assert release_c._clean({'next_id':1,'notes':[{'id':'n_7','text':'first'},{'id':8,'text':'second'}]})['next_id']==9
    with pytest.raises(ValueError,match='duplicate'):
        release_c._clean({'notes':[{'id':'n_7','text':'x'},{'id':7,'text':'y'}]})

def test_release_memory_invalid_json_structure_not_discarded(release_memory):
    release_memory.write_text(json.dumps({'profile':{},'notes':'corrupted'}),encoding='utf8')
    with pytest.raises(ValueError):release_c.load_memory()
    assert list(release_memory.parent.glob('user.json.corrupt-*'))
    with pytest.raises(ValueError):release_c.run_tool({'action':'read'})

def test_release_memory_backup_recovery(release_memory):
    release_c.run_tool({'action':'save','value':'A'})
    release_c.run_tool({'action':'save','value':'B'})
    release_memory.write_text('{ broken JSON',encoding='utf8')
    res=release_c.run_tool({'action':'read'})
    assert len(res['notes'])==1 and res['notes'][0]['text']=='A'
    assert list(release_memory.parent.glob('user.json.corrupt-*'))

def test_release_memory_number_dedup_and_projects(release_memory):
    assert release_c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150000 RUB','project':'work'})['saved']==1
    assert release_c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150001 RUB','project':'work'})['saved']==2
    assert release_c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150000 RUB','project':'other'})['saved']==3

def test_release_memory_tool_error_for_near_duplicate(release_memory):
    release_c.run_tool({'action':'save','value':'A repeated note documenting some design work'})
    payload=release_rpc('tools/call',params={'name':'manage_memory','arguments':{'action':'save','value':'A repeated note documenting some design works'}})
    res=release_c.handle_rpc(payload)['result']
    assert res['isError'] is True
    assert json.loads(res['content'][0]['text'])['not_saved'] is True

def test_release_memory_reject_long_project(release_memory):
    with pytest.raises(ValueError):release_c.run_tool({'action':'save','value':'abc','project':'a'*61})

def test_release_memory_secret_redaction_existing(release_memory):
    release_memory.write_text(json.dumps({'profile':{'private':'password:ABCDEFG12345'},'notes':[]}),encoding='utf8')
    assert release_c.run_tool({'action':'read','category':'profile'})['profile']['private'].startswith('[REDACTED')

def test_release_memory_symlink_refused(release_memory,tmp_path):
    target=tmp_path/'victim';target.write_text('do-not-change')
    try:release_memory.symlink_to(target)
    except (OSError,NotImplementedError):pytest.skip('no symlink support')
    with pytest.raises(OSError):release_c.run_tool({'action':'save','value':'hello'})
    assert target.read_text()=='do-not-change'

def test_release_memory_multithread_process_isolation(tmp_path):
    path=tmp_path/'db.json'; env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def writer(k):
        requests=[release_rpc('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','value':f'v{k}_{i}'}}) for i in range(12)]
        p=subprocess.run([sys.executable,str(release_BASE/'compact_memory_mcp.py')],input='\n'.join(json.dumps(x) for x in requests)+'\n',text=True,capture_output=True,env=env,timeout=20)
        assert p.returncode==0,p.stderr
        assert all(x['result']['isError'] is False for x in map(json.loads,p.stdout.splitlines()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer,range(4)))
    entries=json.loads(path.read_text())['notes']
    assert len(entries)==48 and len({n['id'] for n in entries})==48

def test_release_integration_all(release_memory):
    release_c.run_tool({'action':'save','category':'profile','key':'principal','value':'8000000'})
    release_c.run_tool({'action':'save','category':'profile','key':'rate','value':'12'})
    profile=release_c.run_tool({'action':'read','category':'profile'})['profile']
    val=release_m.calculate(f'P={profile["principal"]}\nr=monthly({profile["rate"]})\npmt(r,120,P)')
    due=release_d.run_tool({'action':'add','datetime':'2024-01-31T12:00:00.123456Z','amount':1,'unit':'months'})['result']
    assert due.startswith('2024-02-29') and '.123456' in due
    release_c.run_tool({'action':'save','value':f'Payment {val}, due {due[:10]}'})
    assert val in release_c.run_tool({'action':'read','query':'Payment'})['notes'][0]['text']

def test_release_memory_old_hex_ids():
    assert release_c._clean({'notes':[{'id':'n_a1b2c3','text':'old memo'}]})['notes'][0]['id']==0xa1b2c3
    assert release_c.arg_id({'id':'n_a1b2c3'})==0xa1b2c3

def test_release_matrix_broadcast_bomb_rejected():
    for expr in ['ones([10000,1])+ones([1,10000])',
                 'matmul(ones([10000,1]),ones([1,10000]))',
                 'dot(ones([10000,1]),ones([1,10000]))']:
        with pytest.raises(ValueError,match='exceed'):
            release_m.calculate(expr)

def test_release_datetime_iana_wall_vs_elapsed_dst():
    base={'action':'add','datetime':'2024-03-09T12:00:00-05:00',
          'timezone':'America/New_York'}
    assert release_d.run_tool(dict(base,unit='days',amount=1))['result']=='2024-03-10T12:00:00-04:00'
    assert release_d.run_tool(dict(base,unit='hours',amount=24))['result']=='2024-03-10T13:00:00-04:00'

def test_release_memory_unknown_nonempty_json_rejected():
    with pytest.raises(ValueError,match='missing profile'):
        release_c._clean({'injected':'nonsense'})



# ================================================================
# Original suite: test_audit_upgraded.py (audit)
# ================================================================
"""Independent audit tests for MCP release. Run with the three .py modules in the same directory."""
import importlib.util, itertools, json, math, os, pathlib, random, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, getcontext
from zoneinfo import ZoneInfo
import numpy as np
import pytest
from jsonschema import Draft202012Validator

audit_BASE=pathlib.Path(os.environ.get('MCP_AUDIT_SOURCE_DIR', str(pathlib.Path(__file__).resolve().parent)))
audit_FILES=['math_calculator','datetime_mcp','compact_memory_mcp']

def audit_load(name):
    spec=importlib.util.spec_from_file_location('audit_'+name,audit_BASE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
audit_m=audit_load('math_calculator'); audit_d=audit_load('datetime_mcp'); audit_c=audit_load('compact_memory_mcp')

@pytest.fixture
def audit_memory(tmp_path, monkeypatch):
    f=tmp_path/'memory.json'
    monkeypatch.setattr(audit_c,'MEMORY_FILE',f)
    monkeypatch.setattr(audit_c,'BACKUP_FILE',f.with_name('memory.json.bak'))
    monkeypatch.setattr(audit_c,'LOCK_FILE',f.with_name('memory.json.lock'))
    monkeypatch.setattr(audit_c,'LEGACY_FILE',tmp_path/'old.json')
    return f

def audit_rpc_handler(mod,req):
    return (mod.handle_rpc if mod is audit_c else mod.handle)(req)

def audit_rpc_request(method,id=1,params=None):
    q={'jsonrpc':'2.0','id':id,'method':method}
    if params is not None:q['params']=params
    return q

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_compile(module):
    compile((audit_BASE/(module+'.py')).read_bytes(),str(audit_BASE/(module+'.py')),'exec')

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

@pytest.mark.parametrize('expr,out',[('2+2*3','8'),('2^10','1024'),('-3^2','-9'),('(-3)^2','9'),('0.1+0.2','0.3'),('sqrt(16)','4'),('factorial(10)','3628800'),('gcd(24,36)','12'),('log(e)','1'),('pi','3.14159265358979'),('2**3**2','512'),('sin(0)','0'),('round(2.5)','2'),('pmt(0,10,100)','10'),('monthly(12)','0.01'),('pct(5)','0.05'),('npv(0,100,50,-20)','130')])
def test_audit_math_examples(expr,out): assert audit_m.calculate(expr)==out

@pytest.mark.parametrize('expr', ['1/0','2**600','2**5000','factorial(1001)','0**-1','__import__("os")','open("/etc/passwd")','(1).__class__','math.__dict__','[x for x in range(9)]','(lambda:4)()','[1,2]','sum([1,2])','9e999','2+nan','3 and 2', '10+'*1000])
def test_audit_math_forbidden(expr):
    with pytest.raises(Exception): audit_m.calculate(expr,'numeric')

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

@pytest.mark.parametrize('date_str,weekday',[('2000-01-01',6),('2024-02-29',4),('1900-03-01',4),('2026-11-05',4),('0001-01-01',1),('9999-12-31',5)])
def test_audit_datetime_weekday(date_str,weekday):
    assert audit_d.run_tool({'action':'weekday','date':date_str})['weekday_number_monday_1']==weekday

@pytest.mark.parametrize('date_str,amount,unit,result',[('2024-01-31T12:00:00Z',1,'months','2024-02-29T12:00:00+00:00'),('2023-01-31T12:00:00Z',1,'months','2023-02-28T12:00:00+00:00'),('2024-02-29T12:00:00Z',1,'years','2025-02-28T12:00:00+00:00'),('2024-03-31T12:00:00Z',-1,'months','2024-02-29T12:00:00+00:00'),('2024-12-31T12:00:00Z',1,'days','2025-01-01T12:00:00+00:00'),('0001-01-01T00:00:00Z',1,'days','0001-01-02T00:00:00+00:00')])
def test_audit_datetime_add_cases(date_str,amount,unit,result):
    assert audit_d.run_tool({'action':'add','datetime':date_str,'amount':amount,'unit':unit})['result']==result

@pytest.mark.parametrize('bad',[{'action':'weekday','date':'2023-02-29'},{'action':'calendar','year':2024,'month':13},{'action':'calendar','year':True,'month':4},{'action':'add','datetime':'2026-01-01T00:00:00','amount':2,'unit':'hours'},{'action':'add','datetime':'2026-01-01T00:00:00Z','amount':1.2,'unit':'months'},{'action':'add','datetime':'2026-01-01T00:00:00Z','amount':float('nan'),'unit':'days'},{'action':'now','timezone':'not-a-zone'},{'action':'difference','start':'2024-01-01T00:00:00','end':'2024-01-02T00:00:00Z'}])
def test_audit_datetime_bad_arguments(bad):
    with pytest.raises((ValueError,OverflowError)): audit_d.run_tool(bad)

@pytest.mark.parametrize('name,offset',[('UTC+05:45',20700),('UTC-03:30',-12600),('Asia/Kathmandu',20700),('UTC',0),('America/New_York',-18000)])
def test_audit_datetime_timezones(name,offset):
    tz,_=audit_d.get_zone(name)
    assert datetime(2024,1,15,tzinfo=tz).utcoffset().total_seconds()==offset

@pytest.mark.parametrize('start,end,seconds',[('1970-01-01T00:00:00Z','1970-01-01T00:00:01Z',1),('2024-03-10T01:30:00-05:00','2024-03-10T03:30:00-04:00',3600),('2024-11-03T01:30:00-04:00','2024-11-03T01:30:00-05:00',3600),('2024-12-31T00:00:00Z','2024-01-01T00:00:00Z',-31536000)])
def test_audit_datetime_difference(start,end,seconds):
    assert audit_d.run_tool({'action':'difference','start':start,'end':end})['seconds']==seconds

def test_audit_datetime_randomized_date_arithmetic():
    r=random.Random(12345)
    for _ in range(600):
        dd=date(r.randint(1900,2100),1,1)+timedelta(days=r.randint(0,364))
        amount=r.randint(-365,365)
        expr=dd.isoformat()+'T08:30:00Z'
        actual=audit_d.run_tool({'action':'add','datetime':expr,'amount':amount,'unit':'days'})['result'][:10]
        assert actual==(dd+timedelta(days=amount)).isoformat()

def test_audit_datetime_calendar_consistency():
    for y in [1,1582,1900,2000,2024,2025,9999]:
        for month in range(1,13):
            out=audit_d.run_tool({'action':'calendar','year':y,'month':month})
            assert set(day for week in out['weeks'] for day in week if day)!=set() or False
            assert sum(bool(day) for week in out['weeks'] for day in week)==__import__('calendar').monthrange(y,month)[1]

def test_audit_datetime_dst_reference():
    # Checks independent zoneinfo reference; difference tool accepts offsets only, so compare it to timezone-aware instants.
    for zone,one,two in [('America/New_York',datetime(2024,3,10,1,30),datetime(2024,3,10,3,30)),('Europe/Berlin',datetime(2024,3,31,1,30),datetime(2024,3,31,3,30)),('Australia/Sydney',datetime(2024,10,6,1,30),datetime(2024,10,6,3,30))]:
        z=ZoneInfo(zone)
        start=one.replace(tzinfo=z); end=two.replace(tzinfo=z)
        tool=audit_d.run_tool({'action':'difference','start':start.isoformat(),'end':end.isoformat()})
        assert tool['seconds']==3600

def test_audit_datetime_dst_fold_reference():
    z=ZoneInfo('America/New_York')
    a=datetime(2024,11,3,1,30,tzinfo=z,fold=0)
    b=datetime(2024,11,3,1,30,tzinfo=z,fold=1)
    assert audit_d.run_tool({'action':'difference','start':a.isoformat(),'end':b.isoformat()})['seconds']==3600

def test_audit_datetime_randomized_duration():
    r=random.Random(456)
    for _ in range(400):
        sec1=r.randint(-1_000_000_000,1_000_000_000)
        sec2=r.randint(-1_000_000_000,1_000_000_000)
        # Avoid platform C runtime limits for negative Unix timestamps on Windows.
        epoch=datetime(1970,1,1,tzinfo=timezone.utc)
        x=(epoch+timedelta(seconds=sec1)).isoformat()
        y=(epoch+timedelta(seconds=sec2)).isoformat()
        assert audit_d.run_tool({'action':'difference','start':x,'end':y})['seconds']==sec2-sec1

def test_audit_memory_crud_and_persist(audit_memory):
    assert audit_c.run_tool({'action':'save','category':'profile','key':'operating system','value':'Windows 11'})['ok']
    assert audit_c.run_tool({'action':'save','category':'note','value':'Тестовые заметки содержат Unicode 日本語','project':'demo'})['saved']==1
    assert audit_c.run_tool({'action':'save','category':'note','id':1,'value':'Тестовый обновлённый текст'})['updated']==1
    assert 'Тестовый обновлённый' in audit_c.run_tool({'action':'read','category':'note'})['notes'][0]['text']
    assert audit_c.run_tool({'action':'delete','id':1})['deleted']==1
    assert audit_c.run_tool({'action':'delete','key':'operating system'})['deleted']=='operating_system'
    assert not audit_c.run_tool({'action':'read'})['notes']
    json.loads(audit_memory.read_text(encoding='utf8'))

def test_audit_memory_duplicates_and_similarity(audit_memory):
    assert audit_c.run_tool({'action':'save','value':'A repeated note documenting some design work'})['saved']==1
    assert audit_c.run_tool({'action':'save','value':'A repeated note documenting some design work'})['duplicate_of']==1
    near=audit_c.run_tool({'action':'save','value':'A repeated note documenting some design works'})
    assert near['not_saved'] and near['similar_to']==1

def test_audit_memory_secret_filter(audit_memory):
    for secret in ['sk-ABCDEF1234567890ABCDEF1234567890','password:Not123456','ghp_Aa1234567890Aa1234567890']:
        with pytest.raises(ValueError): audit_c.run_tool({'action':'save','category':'note','value':secret})
    assert not audit_memory.exists()

def test_audit_memory_budget_and_limits(audit_memory):
    for i in range(20): audit_c.run_tool({'action':'save','value':f'item_{i}'})
    out=audit_c.run_tool({'action':'read'})
    assert len(out['notes'])<=audit_c.NOTES_LIMIT and out['more']>0
    with pytest.raises(ValueError): audit_c.run_tool({'action':'save','value':'x'*601})

def test_audit_memory_randomized_crud(audit_memory):
    r=random.Random(199)
    model={}
    for i in range(80):
        text=f'test note id {i} content {r.randrange(10**8)}'
        ident=audit_c.run_tool({'action':'save','category':'note','value':text})['saved']
        model[ident]=text
    for ident in r.sample(list(model),30):
        audit_c.run_tool({'action':'delete','id':ident})
        del model[ident]
    for ident in r.sample(list(model),20):
        text=f'Changed note id {ident} specific text'
        audit_c.run_tool({'action':'save','id':ident,'value':text})
        model[ident]=text
    state=audit_c.load_memory()
    assert {n['id']:n['text'] for n in state['notes']}==model
    assert len(model)==50

def test_audit_memory_backup_recovery(audit_memory):
    audit_c.run_tool({'action':'save','value':'first note'})
    audit_c.run_tool({'action':'save','value':'second note'})
    assert audit_c.BACKUP_FILE.exists()
    audit_memory.write_text('{invalid json',encoding='utf8')
    recovered=audit_c.run_tool({'action':'read'})
    assert any(n['text']=='first note' for n in recovered['notes'])
    assert audit_memory.exists()
    assert list(audit_memory.parent.glob('memory.json.corrupt-*'))

def test_audit_memory_no_secret_to_stdout(audit_memory):
    audit_c.run_tool({'action':'save','value':'ordinary note'})
    assert 'ordinary note' in audit_c.run_tool({'action':'read'})['notes'][0]['text']

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_stdio_multiplex(module,tmp_path):
    env=os.environ.copy(); env['AI_MEMORY_FILE']=str(tmp_path/'tmp_memory.json')
    commands=[audit_rpc_request('initialize',i,{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}) for i in [1]]
    commands.append({'jsonrpc':'2.0','method':'notifications/initialized'})
    commands.extend([audit_rpc_request('ping',i) for i in range(2,25)])
    commands.append(audit_rpc_request('tools/list',25))
    raw='\n'.join(json.dumps(x,ensure_ascii=False) for x in commands)+'\n'+'{bad json\n'
    p=subprocess.run([sys.executable,'-u',str(audit_BASE/(module+'.py'))], input=raw,capture_output=True,text=True,encoding='utf8',env=env,timeout=20)
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
    p=subprocess.run([sys.executable,str(audit_BASE/(module+'.py'))],input=input_data,capture_output=True,text=True,encoding='utf8',env=env,timeout=15)
    assert p.returncode==0,p.stderr
    for line in p.stdout.splitlines():
        reply=json.loads(line)
        assert reply.get('jsonrpc')=='2.0'
        assert 'error' in reply

def test_audit_memory_concurrent_writers(tmp_path):
    path=tmp_path/'shared.json'; env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def write_one(k):
        commands=[]
        for i in range(15):
            commands.append(audit_rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','category':'note','value':f'z{k}{i:02}'}}))
        p=subprocess.run([sys.executable,str(audit_BASE/'compact_memory_mcp.py')],input='\n'.join(json.dumps(q) for q in commands)+'\n',capture_output=True,text=True,env=env,timeout=25)
        assert p.returncode==0,p.stderr
        results=[json.loads(x) for x in p.stdout.splitlines()]
        assert len(results)==15
        assert all(x['result']['isError'] is False for x in results)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_one,range(4)))
    obj=json.loads(path.read_text(encoding='utf8'))
    assert len(obj['notes'])==60
    assert len({x['id'] for x in obj['notes']})==60

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

# Regression probes that document undesired behavior without silently treating it as a passing fix.
def test_audit_expected_math_multistatement_assignments():
    assert audit_m.calculate('P=8000000\nr=0.01\nP*r')=='80000'

def test_audit_expected_math_pmt_near_zero():
    assert math.isclose(float(audit_m.calculate('pmt(1e-17,120,1000)')),1000/120,rel_tol=1e-12)

def test_audit_expected_math_noninteger_periods_rejected():
    with pytest.raises(ValueError): audit_m.calculate('fv_growing_rent(0.01,2.5,100,40,0.06)')

def test_audit_expected_datetime_microseconds():
    assert audit_d.run_tool({'action':'add','datetime':'2024-01-01T00:00:00.123456Z','unit':'seconds','amount':1})['result'].endswith('01.123456+00:00')

def test_audit_expected_memory_no_secret_in_project(audit_memory):
    with pytest.raises(ValueError): audit_c.run_tool({'action':'save','value':'public note','project':'sk-ABCDEF1234567890ABCDEF1234567890'})

def test_audit_expected_memory_reject_structurally_invalid_store(audit_memory):
    audit_memory.write_text(json.dumps({'notes':'not a list','profile':{}}),encoding='utf8')
    with pytest.raises(ValueError): audit_c.load_memory()

def test_audit_expected_memory_legacy_ids_preserved():
    assert audit_c._clean({'notes':[{'id':'n_7','text':'old'}], 'next_id':1})['notes'][0]['id']==7

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_expected_jsonrpc_version_validation(module):
    mod={'math_calculator':audit_m,'datetime_mcp':audit_d,'compact_memory_mcp':audit_c}[module]
    res=audit_rpc_handler(mod,{'jsonrpc':'1.0','id':42,'method':'ping'})
    assert res['error']['code']==-32600

@pytest.mark.parametrize('module',audit_FILES)
def test_audit_expected_jsonrpc_id_validation(module):
    mod={'math_calculator':audit_m,'datetime_mcp':audit_d,'compact_memory_mcp':audit_c}[module]
    res=audit_rpc_handler(mod,{'jsonrpc':'2.0','id':{'oops':1},'method':'ping'})
    assert res['error']['code']==-32600

def test_audit_expected_matrix_nonfinite_rejected():
    with pytest.raises(ValueError):audit_m.calculate('array([1e309])')

def test_audit_expected_memory_negotiate_supported_protocol():
    res=audit_c.handle_rpc(audit_rpc_request('initialize',params={'protocolVersion':'2099-01-01','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}))
    assert res['result']['protocolVersion'] in ['2024-11-05','2025-03-26','2025-06-18','2025-11-25']

def test_audit_expected_memory_distinct_numbers_preserved(audit_memory):
    a=audit_c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150000 RUB'})
    b=audit_c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150001 RUB'})
    assert a.get('saved')==1 and b.get('saved')==2

@pytest.mark.skipif(os.name == "nt", reason="POSIX 0600 bits are not a Windows ACL security check")
def test_audit_expected_memory_private_permission(audit_memory):
    import stat
    previous=os.umask(0o022)
    try:audit_c.run_tool({'action':'save','value':'hello'})
    finally:os.umask(previous)
    assert audit_memory.stat().st_mode & (stat.S_IRGRP|stat.S_IROTH)==0

def test_audit_memory_rename_failure_is_atomic(audit_memory,monkeypatch):
    audit_c.run_tool({'action':'save','value':'original-note'})
    original=audit_memory.read_bytes()
    actual_replace=audit_c.os.replace
    def fail_replace(src,dst):raise PermissionError('injected rename failure')
    monkeypatch.setattr(audit_c.os,'replace',fail_replace)
    with pytest.raises(PermissionError):audit_c.run_tool({'action':'save','value':'new-note'})
    assert audit_memory.read_bytes()==original
    monkeypatch.setattr(audit_c.os,'replace',actual_replace)
    assert audit_c.run_tool({'action':'read'})['notes'][0]['text']=='original-note'

def test_audit_memory_read_write_consistency(tmp_path):
    path=tmp_path/'twin.json';env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def process(k):
        commands=[]
        if k==0:
            for i in range(20):commands.append(audit_rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','value':f'q{i:02}'}}))
        else:
            for i in range(20):commands.append(audit_rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'read'}}))
        p=subprocess.run([sys.executable,str(audit_BASE/'compact_memory_mcp.py')],input='\n'.join(map(json.dumps,commands))+'\n',capture_output=True,text=True,env=env,timeout=20)
        assert p.returncode==0,p.stderr
        data=[json.loads(line) for line in p.stdout.splitlines()]
        assert len(data)==20 and all(v['result']['isError'] is False for v in data)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(process,range(2)))
    assert len(json.loads(path.read_text(encoding='utf8'))['notes'])==20

def test_audit_math_heavy_timeout_liveness():
    with pytest.raises(ValueError,match='timed out'):
        audit_m.run_heavy(audit_m.preprocess('expand((x+1)^500)'), 'symbolic', timeout=.0001)
    assert audit_m.calculate('det([[1]])') == '1'

def test_audit_datetime_missing_tzdb_simulation(tmp_path):
    env=os.environ.copy();env['PYTHONTZPATH']=str(tmp_path/'nonexistent_tz_path')
    request1=audit_rpc_request('tools/call',1,{'name':'datetime_calendar','arguments':{'action':'now'}})
    request2=audit_rpc_request('tools/call',2,{'name':'datetime_calendar','arguments':{'action':'now','timezone':'UTC+0'}})
    p=subprocess.run([sys.executable,'-S',str(audit_BASE/'datetime_mcp.py')],input=json.dumps(request1)+'\n'+json.dumps(request2)+'\n',text=True,capture_output=True,env=env,timeout=10)
    assert p.returncode==0,p.stderr
    a,b=[json.loads(v)['result'] for v in p.stdout.splitlines()]
    assert a['isError'] is False
    assert b['isError'] is False

def test_audit_integration_finance_calendar_12_dates():
    amount=float(audit_m.calculate('pmt(monthly(12),120,8000000)'))
    assert amount>0
    start='2024-01-31T12:00:00Z'
    due=[audit_d.run_tool({'action':'add','datetime':start,'amount':i,'unit':'months'})['result'] for i in range(1,13)]
    assert len(set(due))==12
    assert due[0].startswith('2024-02-29')
    assert due[-1].startswith('2025-01-31')
    total_12=amount*12
    assert total_12>0

def test_audit_integration_memory_finance(audit_memory):
    for key,val in [('principal','8000000'),('annual_percent','12'),('periods','120')]:
        audit_c.run_tool({'action':'save','category':'profile','key':key,'value':val})
    values=audit_c.run_tool({'action':'read','category':'profile'})['profile']
    numeric=f"pmt(monthly({values['annual_percent']}),{values['periods']},{values['principal']})"
    computed=audit_m.calculate(numeric)
    assert float(computed)>10000
    assert audit_c.run_tool({'action':'save','category':'note','value':f'Calculated monthly payment {computed}'})['ok']
    assert computed in audit_c.run_tool({'action':'read','query':'payment'})['notes'][0]['text']

def test_audit_integration_memory_datetime(audit_memory):
    audit_c.run_tool({'action':'save','category':'profile','key':'deadline','value':'2026-12-01T00:00:00Z'})
    deadline=audit_c.run_tool({'action':'read','category':'profile'})['profile']['deadline']
    days=audit_d.run_tool({'action':'difference','start':'2026-11-01T00:00:00Z','end':deadline})['days']
    assert days==30
    audit_c.run_tool({'action':'save','category':'profile','key':'days_to_deadline','value':str(int(days))})
    assert audit_c.run_tool({'action':'read','category':'profile'})['profile']['days_to_deadline']=='30'



# ================================================================
# Original suite: test_qwen_optimization.py (qwen)
# ================================================================
"""Regression tests for the minimal Qwen3.5-4B tool-description update.

Put alongside the three MCP modules; no real user memory is touched.
"""
import json
import math

import compact_memory_mcp as memory
import datetime_mcp as calendar
import math_calculator as calc


def qwen_rpc_tools(handler):
    result = handler({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "error" not in result
    return result["result"]["tools"]


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



# ================================================================
# Original suite: test_regressions.py (regression)
# ================================================================
"""Regressions for P1/P2/P3 findings in the 2026-11-05 review.
Run: python -m pytest -q test_regressions.py
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

regression_BASE = Path(__file__).resolve().parent


def regression_load(name):
    spec = importlib.util.spec_from_file_location(name, regression_BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


regression_m = regression_load('math_calculator')
regression_d = regression_load('datetime_mcp')
regression_c = regression_load('compact_memory_mcp')
regression_SERVERS = [(regression_m, regression_m.handle, 'calculate'), (regression_d, regression_d.handle, 'datetime_calendar'),
           (regression_c, regression_c.handle_rpc, 'manage_memory')]


@pytest.fixture
def regression_memory(tmp_path, monkeypatch):
    f = tmp_path / 'state.json'
    monkeypatch.setattr(regression_c, 'MEMORY_FILE', f)
    monkeypatch.setattr(regression_c, 'BACKUP_FILE', f.with_name(f.name + '.bak'))
    monkeypatch.setattr(regression_c, 'LOCK_FILE', f.with_name(f.name + '.lock'))
    monkeypatch.setattr(regression_c, 'LEGACY_FILE', tmp_path / 'legacy.json')
    return f


def regression_modern(method, *, args=None, version='2026-07-28', id_=3):
    params = {'_meta': {'io.modelcontextprotocol/protocolVersion': version,
                       'io.modelcontextprotocol/clientCapabilities': {}}}
    if args:
        params.update(args)
    return {'jsonrpc': '2.0', 'id': id_, 'method': method, 'params': params}


def regression_legacy(method, *, params=None):
    return {'jsonrpc': '2.0', 'id': 3, 'method': method, 'params': params}


@pytest.mark.parametrize('module,handle,tool_name', regression_SERVERS)
def test_regression_modern_discovery_result_required_fields(module, handle, tool_name, regression_memory):
    result = handle(regression_modern('server/discover'))['result']
    assert result['resultType'] == 'complete'
    assert result['supportedVersions'] == ['2026-07-28']
    assert result['capabilities'] == {'tools': {}}
    assert result['ttlMs'] == 0 and result['cacheScope'] == 'private'
    assert result['_meta']['io.modelcontextprotocol/serverInfo']['name'] == module.SERVER_NAME


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


def test_regression_memory_recovery_is_explicit_and_persistent(regression_memory, monkeypatch):
    regression_c.run_tool({'action': 'save', 'value': 'note one'})
    regression_c.run_tool({'action': 'save', 'value': 'note two'})
    raw = json.loads(regression_memory.read_text(encoding='utf-8'))
    raw['notes'][1]['created_at'] = 123  # logically invalid, valid JSON
    regression_memory.write_text(json.dumps(raw), encoding='utf-8')
    res = regression_c.run_tool({'action': 'read'})
    assert 'warning' in res and 'restored from a backup' in res['warning']
    assert 'note one' in str(res)
    assert 'note two' not in str(res)
    quarantined = list(regression_memory.parent.glob(regression_memory.name + '.corrupt-*'))
    assert len(quarantined) == 1
    assert 'note two' in quarantined[0].read_text(encoding='utf-8')
    res2 = regression_c.run_tool({'action': 'save', 'value': 'note three'})
    assert 'warning' not in res2  # one notice per server process
    assert 'warning' not in regression_c.run_tool({'action': 'read'})
    # Emulates new Python process importing module again.
    fresh = regression_load('compact_memory_mcp')
    monkeypatch.setattr(fresh, 'MEMORY_FILE', regression_memory)
    monkeypatch.setattr(fresh, 'BACKUP_FILE', regression_c.BACKUP_FILE)
    monkeypatch.setattr(fresh, 'LOCK_FILE', regression_c.LOCK_FILE)
    assert 'warning' in fresh.run_tool({'action': 'read'})
    assert regression_c._recovery_notice_path().exists()


def test_regression_memory_cannot_resurrect_legacy_after_quarantine(tmp_path):
    """Real stdio subprocess: home directory and legacy path must be genuine."""
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    old = home / '.ai_memory_v1.2.json'
    old.write_text(json.dumps({'profile': {'old': 'legacy'}, 'notes': []}), encoding='utf-8')
    env = {**os.environ, 'HOME': str(home), 'USERPROFILE': str(home)}
    # Windows Path.home() uses USERPROFILE, not HOME. Keep both so this test
    # also isolates the real stdio process on Linux/macOS.
    env.pop('AI_MEMORY_FILE', None)
    # Fail closed: never start the memory server if Python resolves '~' to the
    # real user profile rather than our disposable test directory.
    probe = subprocess.run(
        [sys.executable, '-c', 'from pathlib import Path; print(Path.home())'],
        text=True, capture_output=True, env=env, timeout=10, check=True,
    )
    assert Path(probe.stdout.strip()).resolve() == home.resolve(), (
        f'Test home isolation failed: expected {home}, got {probe.stdout.strip()!r}'
    )
    reqs = [regression_legacy('tools/call', params={'name':'manage_memory','arguments':{'action':'read'}})]

    def send(*items):
        proc = subprocess.run([sys.executable, str(regression_BASE / 'compact_memory_mcp.py')],
                              input='\n'.join(json.dumps(x) for x in items)+'\n',
                              text=True, capture_output=True, env=env, timeout=10)
        assert proc.returncode == 0, proc.stderr
        return [json.loads(x) for x in proc.stdout.splitlines()]

    assert 'legacy' in send(reqs[0])[0]['result']['content'][0]['text']
    primary = home / '.ai_memory.json'
    assert primary.exists()
    assert (home / '.ai_memory_v1.2.json.migrated').exists()
    assert (home / '.ai_memory.json.migration-complete').exists()
    primary.write_text('{broken', encoding='utf-8')
    # No .bak exists; the first read quarantines, and second must NOT import legacy.
    first, second = send(reqs[0],reqs[0])
    assert first['result']['isError'] and second['result']['isError']
    assert 'quarantined' in second['result']['content'][0]['text'].lower()
    assert not primary.exists()


def test_regression_memory_does_not_start_empty_over_orphaned_backup(regression_memory):
    regression_c.BACKUP_FILE.write_text(json.dumps({'profile':{'critical':'kept'},'notes':[]}), encoding='utf8')
    with pytest.raises(ValueError, match='backup exists'):
        regression_c.load_memory()


def test_regression_memory_snake_case_search_profile_and_notes(regression_memory):
    regression_c.run_tool({'action':'save', 'category':'profile',
                'key':'favorite programming language', 'value':'Python'})
    assert 'favorite_programming_language' in regression_c.run_tool({'action':'read', 'query':'programming',
                                                        'category':'profile'})['profile']
    regression_c.run_tool({'action':'save','value':'a note','project':'my_project_config'})
    assert len(regression_c.run_tool({'action':'read','query':'config','category':'note'})['notes']) == 1


def test_regression_memory_empty_project_does_not_clear_on_text_edit(regression_memory):
    nid = regression_c.run_tool({'action':'save','value':'Keep this note', 'project':'tgbot'})['saved']
    assert regression_c.run_tool({'action':'save','id':nid, 'value':'Updated note', 'project':''})['updated'] == nid
    row = regression_c.run_tool({'action':'read','query':'Updated'})['notes'][0]
    assert row['text'] == 'Updated note' and row['project'] == 'tgbot'
    assert regression_c.load_memory()['notes'][0]['project'] == 'tgbot'
    with pytest.raises(ValueError, match='nothing to update'):
        regression_c.run_tool({'action':'save','id':nid,'project':''})


def test_regression_memory_explicitly_clear_project_tag(regression_memory):
    nid = regression_c.run_tool({'action':'save','value':'Keep this note', 'project':'old_tag'})['saved']
    assert regression_c.run_tool({'action':'save','id':nid, 'clear_project':True})['updated'] == nid
    assert 'project' not in regression_c.run_tool({'action':'read','query':'Keep'})['notes'][0]
    assert regression_c.load_memory()['notes'][0]['project'] is None
    assert regression_c.run_tool({'action':'save','id':nid,'clear_project':True})['unchanged'] == nid


def test_regression_memory_clear_project_rejects_ambiguous_or_invalid_actions(regression_memory):
    nid = regression_c.run_tool({'action':'save','value':'Keep this note', 'project':'old_tag'})['saved']
    with pytest.raises(ValueError, match='cannot set'):
        regression_c.run_tool({'action':'save','id':nid,'project':'new_tag','clear_project':True})
    with pytest.raises(ValueError, match='requires an existing note'):
        regression_c.run_tool({'action':'save','value':'new','clear_project':True})
    with pytest.raises(ValueError, match='true or false'):
        regression_c.run_tool({'action':'save','id':nid,'clear_project':'true'})
    assert regression_c.load_memory()['notes'][0]['project'] == 'old_tag'


def test_regression_memory_project_schema_explains_explicit_clear(regression_memory):
    schema = regression_c.TOOL_SCHEMA['properties']
    assert schema['clear_project']['type'] == 'boolean'
    assert 'empty string' in schema['project']['description']


@pytest.mark.parametrize('sentence', ['password - manager Bitwarden','pass: true'])
def test_regression_memory_harmless_secret_detector_phrases(regression_memory, sentence):
    assert regression_c.run_tool({'action':'save','value':sentence})['ok']


@pytest.mark.parametrize('secret', [
    'password: abcd1234',
    'sk-' + 'Ab0'*10,
    'password - manager hunter2secret99',
    'password - manager Bitwarden hunter2secret99',
    'pass: true hunter2secret99',
])
def test_regression_memory_secret_rejection_preserved(regression_memory, secret):
    with pytest.raises(ValueError, match='secret'):
        regression_c.run_tool({'action':'save','value':secret})


def test_regression_datetime_initialize_explicit_null():
    result = regression_d.handle(regression_legacy('initialize',params=None))['result']
    assert result['protocolVersion'] in regression_d.SUPPORTED_PROTOCOLS


def test_regression_datetime_timezone_description_lists_actions():
    result = regression_d.handle(regression_legacy('tools/list',params={}))['result']
    desc = result['tools'][0]['inputSchema']['properties']['timezone']['description']
    for action in ('convert','localize','timestamp','add','now'):
        assert action in desc


def test_regression_datetime_unknown_action_lists_supported_actions():
    with pytest.raises(ValueError, match='convert, timestamp, or localize'):
        regression_d.run_tool({'action':'not_real'})


@pytest.mark.parametrize('module,handle,tool_name',regression_SERVERS)
def test_regression_json_string_arguments_consistently_supported(module,handle,tool_name,regression_memory):
    args = ({'expr':'2+2'} if tool_name == 'calculate' else
            {'action':'now'} if tool_name == 'datetime_calendar' else
            {'action':'read'})
    result=handle(regression_legacy('tools/call',params={'name':tool_name,'arguments':json.dumps(args)}))['result']
    assert result['isError'] is False, result


@pytest.mark.parametrize('module,handle,tool_name',regression_SERVERS)
def test_regression_invalid_json_string_returns_tool_error(module,handle,tool_name,regression_memory):
    result=handle(regression_legacy('tools/call',params={'name':tool_name,'arguments':'{"invalid'}))['result']
    assert result['isError'] is True


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

@pytest.mark.skipif(os.name == 'nt', reason='POSIX permissions only')
def test_regression_memory_shared_directory_requires_opt_in(tmp_path, monkeypatch):
    folder = tmp_path / 'trusted_shared'
    folder.mkdir()
    folder.chmod(0o777)
    path = folder / 'memo.json'
    monkeypatch.setattr(regression_c, 'MEMORY_FILE', path)
    monkeypatch.setattr(regression_c, 'BACKUP_FILE', folder / 'memo.json.bak')
    monkeypatch.setattr(regression_c, 'LOCK_FILE', folder / 'memo.json.lock')
    monkeypatch.delenv('AI_MEMORY_TRUST_SHARED_DIR', raising=False)
    try:
        with pytest.raises(OSError, match='group/other writable'):
            regression_c.run_tool({'action': 'save', 'value': 'first'})
        monkeypatch.setenv('AI_MEMORY_TRUST_SHARED_DIR','1')
        assert regression_c.run_tool({'action': 'save', 'value': 'first'})['ok']
    finally:
        folder.chmod(0o700)


def test_regression_memory_migration_marker_blocks_legacy_resurrection_even_if_old_copy_exists(regression_memory):
    # Simulates a failed rename after an otherwise completed import.
    regression_c._mark_legacy_migration()
    regression_c.LEGACY_FILE.write_text(json.dumps({'profile': {'old': 'legacy'}, 'notes': []}), encoding='utf8')
    with pytest.raises(ValueError, match='migration'):
        regression_c.load_memory()


def test_regression_memory_backup_write_failure_does_not_commit_new_changes(regression_memory, monkeypatch):
    regression_c.run_tool({'action':'save','value':'first'})
    saved = json.loads(regression_memory.read_text(encoding='utf8'))
    def broken_copy(*args, **kwargs):
        raise OSError('simulated backup I/O failure')
    monkeypatch.setattr(regression_c, '_private_copy', broken_copy)
    with pytest.raises(OSError, match='backup I/O failure'):
        regression_c.run_tool({'action':'save','value':'second'})
    assert json.loads(regression_memory.read_text(encoding='utf8')) == saved


def test_regression_memory_backup_orphan_error_explains_safe_reset(regression_memory):
    regression_c.BACKUP_FILE.write_text('metadata', encoding='utf8')
    with pytest.raises(ValueError) as raised:
        regression_c.load_memory()
    message = str(raised.value)
    for fragment in (regression_memory.name, regression_c.BACKUP_FILE.name, '.migration-complete',
                     '.recovery-warning.json', '.corrupt-*', regression_c.LEGACY_FILE.name,
                     'first archive', 'clean reset'):
        assert fragment in message


def test_regression_memory_failed_migration_write_leaves_legacy_retryable(regression_memory, monkeypatch):
    regression_c.LEGACY_FILE.write_text(json.dumps({'profile': {'old': 'legacy'}, 'notes': []}), encoding='utf8')
    original = regression_c._write_atomic
    def no_space(*_args, **_kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr(regression_c, '_write_atomic', no_space)
    with pytest.raises(OSError, match='disk full'):
        regression_c._import_legacy()
    assert not regression_c._migration_marker_path().exists()
    assert not regression_c._migration_pending_path().exists()
    assert not regression_memory.exists()
    assert regression_c.LEGACY_FILE.exists()
    monkeypatch.setattr(regression_c, '_write_atomic', original)
    imported = regression_c._import_legacy()
    assert imported['profile']['old'] == 'legacy'
    assert regression_memory.exists()
    assert regression_c._migration_marker_path().exists()
    assert not regression_c._migration_pending_path().exists()
    assert regression_c.LEGACY_FILE.with_name(regression_c.LEGACY_FILE.name + '.migrated').exists()


def test_regression_memory_recovery_warning_reappears_in_new_process_only(regression_memory, monkeypatch):
    regression_c.run_tool({'action':'save', 'value':'A'})
    regression_c.run_tool({'action':'save', 'value':'B'})
    regression_memory.write_text('{not valid', encoding='utf8')
    assert 'warning' in regression_c.run_tool({'action':'read'})
    assert 'warning' not in regression_c.run_tool({'action':'read'})
    assert regression_c._recovery_notice_path().exists()
    fresh = regression_load('compact_memory_mcp')
    for name in ('MEMORY_FILE', 'BACKUP_FILE', 'LOCK_FILE', 'LEGACY_FILE'):
        monkeypatch.setattr(fresh, name, getattr(regression_c, name))
    assert 'warning' in fresh.run_tool({'action':'read'})
    assert 'warning' not in fresh.run_tool({'action':'read'})
    assert regression_c._recovery_notice_path().exists()


def test_regression_memory_interrupted_migration_never_resurrects_legacy(regression_memory):
    regression_c.LEGACY_FILE.write_text(json.dumps({'profile':{'old':'stale'},'notes':[]}), encoding='utf8')
    regression_c._write_migration_marker(regression_c._migration_pending_path())
    with pytest.raises(ValueError, match='interrupted migration'):
        regression_c.load_memory()
    assert not regression_memory.exists()
    assert regression_c.LEGACY_FILE.exists()


def test_regression_memory_migration_marker_failure_preserves_pending_guard(regression_memory, monkeypatch):
    regression_c.LEGACY_FILE.write_text(json.dumps({'profile':{'old':'legacy'},'notes':[]}), encoding='utf8')
    def failed_marker():
        raise OSError('simulated marker creation failure')
    monkeypatch.setattr(regression_c, '_mark_legacy_migration', failed_marker)
    with pytest.raises(OSError, match='marker creation'):
        regression_c._import_legacy()
    assert regression_memory.exists()
    assert regression_c._migration_pending_path().exists()
    assert not regression_c._migration_marker_path().exists()
    regression_memory.unlink()  # emulate lost primary; never reimport stale legacy
    with pytest.raises(ValueError, match='interrupted migration'):
        regression_c.load_memory()


def test_regression_memory_uncertain_migration_commit_keeps_pending_guard(regression_memory, monkeypatch):
    regression_c.LEGACY_FILE.write_text(json.dumps({'profile': {'old':'source'}, 'notes':[]}), encoding='utf8')
    original = regression_c._write_atomic
    def commit_then_fail(data):
        original(data)
        raise OSError('simulated post-commit I/O failure')
    monkeypatch.setattr(regression_c, '_write_atomic', commit_then_fail)
    with pytest.raises(OSError, match='post-commit'):
        regression_c._import_legacy()
    assert regression_memory.exists() and regression_c._migration_pending_path().exists()
    assert regression_c.LEGACY_FILE.exists()  # never retired until migration is confirmed
    regression_memory.unlink()
    with pytest.raises(ValueError, match='interrupted migration'):
        regression_c.load_memory()


# ============================================================================
# Security and integrity regressions (2026-11-06)
# ============================================================================
import compact_memory_mcp as security_memory
import math_calculator as security_math


@pytest.fixture
def security_memory_file(tmp_path, monkeypatch):
    """Never read or write a real LM Studio memory profile during tests."""
    f = tmp_path / "isolated_memory.json"
    monkeypatch.setattr(security_memory, "MEMORY_FILE", f)
    monkeypatch.setattr(security_memory, "BACKUP_FILE", f.with_name(f.name + ".bak"))
    monkeypatch.setattr(security_memory, "LOCK_FILE", f.with_name(f.name + ".lock"))
    monkeypatch.setattr(security_memory, "LEGACY_FILE", tmp_path / "no_legacy.json")
    return f


@pytest.mark.parametrize("expression", [
    "round(5,-100000000)", "round(5,-10000000)",
    "round(5,10000000)", "round(5,1025)",
    "a=5; round(a,-100000000)",
])
def test_security_numeric_round_bounds_and_liveness(expression):
    with pytest.raises(ValueError, match="ndigits"):
        security_math.calculate(expression)
    assert security_math.calculate("2+2") == "4"


@pytest.mark.parametrize("expression,expected", [
    ("round(12.345,2)", "12.35"),
    ("round(25,-1)", "20"),
    ("round(2.5)", "2"),
    ("round(5,-1000)", "0"),
])
def test_security_round_normal_behavior(expression,expected):
    assert security_math.calculate(expression) == expected


@pytest.mark.parametrize("key,value", [
    ("password", "example123"),
    ("DB_PASSWORD", "example123"),
    ("dbPassword", "example123"),
    ("apiKey", "example123"),
    ("refresh_token", "example123"),
    ("client_secret", "example123"),
])
def test_security_profile_key_value_pair_is_rejected(security_memory_file,key,value):
    with pytest.raises(ValueError,match="secret"):
        security_memory.run_tool({"action":"save","category":"profile","key":key,"value":value})
    assert not security_memory_file.exists()


@pytest.mark.parametrize("text", [
    "github_pat_" + "A"*35,
    "glpat-" + "a"*20,
    "npm_" + "Ab2"*10,
    "AIza" + "A"*35,
    "-----BEGIN PRIVATE KEY-----",
    "1234abcd"*5,  # unlabelled hex is still treated as potentially secret
])
def test_security_structured_secrets_are_rejected(security_memory_file,text):
    with pytest.raises(ValueError,match="secret"):
        security_memory.run_tool({"action":"save","category":"note","value":text})


@pytest.mark.parametrize("key,value", [
    ("git_sha", "a1b2c3d4"*5),
    ("github_sha", "a1b2c3d4"*5),
    ("uuid", "0123456789abcdef"*2),
    ("password_manager", "Bitwarden"),
])
def test_security_harmless_profile_identifiers(security_memory_file,key,value):
    assert security_memory.run_tool({"action":"save","category":"profile","key":key,"value":value})["ok"]
    read=security_memory.run_tool({"action":"read","category":"profile"})
    assert read["profile"][key] == value


@pytest.mark.parametrize("text", [
    "git commit: " + "a1b2c3d4"*5,
    "uuid: " + "0123456789abcdef"*2,
])
def test_security_harmless_identifiers_in_notes(security_memory_file,text):
    assert security_memory.run_tool({"action":"save","value":text})["ok"]
    read=security_memory.run_tool({"action":"read","category":"note"})
    assert read["notes"][0]["text"] == text


def test_security_oversize_storage_fails_before_backup_or_replace(security_memory_file,monkeypatch):
    initial = security_memory.run_tool({"action":"save","value":"first-" + "g"*150})
    assert initial["ok"]
    before = security_memory_file.read_bytes()
    monkeypatch.setattr(security_memory,"MAX_STORE_BYTES",len(before)+40)
    with pytest.raises(ValueError, match="safety limit"):
        security_memory.run_tool({"action":"save","value":"second-" + "k"*150})
    assert security_memory_file.read_bytes() == before
    assert not security_memory.BACKUP_FILE.exists()
    assert len(security_memory.run_tool({"action":"read"})["notes"]) == 1


def test_security_direct_atomic_writer_respects_read_limit(security_memory_file,monkeypatch):
    security_memory.run_tool({"action":"save","value":"baseline"})
    before=security_memory_file.read_bytes()
    monkeypatch.setattr(security_memory,"MAX_STORE_BYTES",len(before)+10)
    data=security_memory.load_memory()
    data["notes"][0]["text"] = "x"*200
    with pytest.raises(ValueError,match="safety limit"):
        security_memory._write_atomic(data)
    assert security_memory_file.read_bytes() == before


def test_security_actual_multibyte_store_limit(security_memory_file):
    """A full, otherwise valid UTF-8 state must be refused before disk mutation."""
    security_memory.run_tool({"action":"save", "value":"baseline"})
    before = security_memory_file.read_bytes()
    timestamp = "2026-11-06T00:00:00+00:00"
    data = {
        "version": "1.4", "profile": {}, "next_id": 301,
        "notes": [
            {"id": i, "text": f"{i}:" + "漢" * 590, "project": None,
             "created_at": timestamp, "updated_at": timestamp}
            for i in range(1, 301)
        ]
    }
    assert len(json.dumps(data,ensure_ascii=False,indent=1).encode("utf-8")) > security_memory.MAX_STORE_BYTES
    with pytest.raises(ValueError,match="safety limit"):
        security_memory.save_memory(data)
    assert security_memory_file.read_bytes() == before
    assert len(security_memory.run_tool({"action":"read"})["notes"]) == 1


# ============================================================================
# Pre-push hardening: LLM token-metadata fields are not credential fields
# ============================================================================
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


@pytest.mark.parametrize("key", [
    "access_token", "refresh_token", "session_token", "auth_token",
    "api_key", "dbPassword", "token_limit_secret", "password_manager_token",
])
def test_security_credential_fields_still_rejected(security_memory_file, key):
    with pytest.raises(ValueError, match="secret"):
        security_memory.run_tool({
            "action": "save", "category": "profile", "key": key, "value": "4096 tokens",
        })
    assert not security_memory_file.exists()


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


# ============================================================================
# Final pre-push regressions: credential patterns, legacy handshake, worker
# ============================================================================

@pytest.mark.parametrize("value", [
    "sk_live_" + "A1b2" * 12,
    "sk_test_" + "A1b2" * 12,
    "rk_live_" + "A1b2" * 12,
    "hf_" + "Ab12" * 9,
    "Authorization: Bearer " + "Ab12" * 9,
    "postgres://user:pass@localhost:5432/example",
    "postgresql://user:pa%40ss@db.example.com/test",
    "redis://default:secret@localhost:6379/0",
    "https://alice:password123@example.com/resource",
    "my password is hunter2-secret",
    "my password is abc1",
])
def test_prepush_credentials_blocked_in_note_and_profile(security_memory_file, value):
    for args in (
        {"action": "save", "category": "note", "value": "Note: " + value},
        {"action": "save", "category": "profile", "key": "token_limit", "value": value},
    ):
        with pytest.raises(ValueError, match="secret"):
            security_memory.run_tool(args)
    assert not security_memory_file.exists()


@pytest.mark.parametrize("value", [
    "My password is not saved here",
    "Bearer token authentication is supported",
    "The Stripe product is not a payment key",
    "postgres://localhost:5432/mydb",
    "hf model identifier",
])
def test_prepush_harmless_prose_not_rejected(security_memory_file, value):
    assert security_memory.run_tool({"action": "save", "category": "note", "value": value})["ok"]


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


def test_prepush_worker_stderr_inherits_parent():
    import inspect
    source = inspect.getsource(security_math.run_heavy)
    assert 'stderr=None' in source
    assert 'stderr=subprocess.DEVNULL' not in source


def test_prepush_worker_resource_limits_are_independent(monkeypatch):
    """Mock resource: even if RLIMIT_CPU fails, RLIMIT_AS is attempted."""
    import types
    import io
    import sys as _sys
    calls = []
    def fake_setrlimit(kind, bounds):
        calls.append((kind, bounds))
        if kind == 1:
            raise OSError("not supported")
    fake = types.SimpleNamespace(RLIMIT_CPU=1, RLIMIT_AS=2, setrlimit=fake_setrlimit)
    monkeypatch.setitem(_sys.modules, 'resource', fake)
    monkeypatch.setattr(security_math.os, 'name', 'posix')
    monkeypatch.setattr(_sys, 'stdin', io.StringIO(''))
    monkeypatch.setattr(_sys, 'stderr', io.StringIO())
    security_math.worker_main()
    assert [x[0] for x in calls] == [1, 2]
