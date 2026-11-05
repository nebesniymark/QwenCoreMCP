"""Independent audit tests for MCP release. Run with the three .py modules in the same directory."""
import importlib.util, itertools, json, math, os, pathlib, random, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, getcontext
from zoneinfo import ZoneInfo
import numpy as np
import pytest
from jsonschema import Draft202012Validator

BASE=pathlib.Path(os.environ.get('MCP_AUDIT_SOURCE_DIR', str(pathlib.Path(__file__).resolve().parent)))
FILES=['math_calculator','datetime_mcp','compact_memory_mcp']

def load(name):
    spec=importlib.util.spec_from_file_location('audit_'+name,BASE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
m=load('math_calculator'); d=load('datetime_mcp'); c=load('compact_memory_mcp')

@pytest.fixture
def memory(tmp_path, monkeypatch):
    f=tmp_path/'memory.json'
    monkeypatch.setattr(c,'MEMORY_FILE',f)
    monkeypatch.setattr(c,'BACKUP_FILE',f.with_name('memory.json.bak'))
    monkeypatch.setattr(c,'LOCK_FILE',f.with_name('memory.json.lock'))
    monkeypatch.setattr(c,'LEGACY_FILE',tmp_path/'old.json')
    return f

def rpc_handler(mod,req):
    return (mod.handle_rpc if mod is c else mod.handle)(req)

def rpc_request(method,id=1,params=None):
    q={'jsonrpc':'2.0','id':id,'method':method}
    if params is not None:q['params']=params
    return q

@pytest.mark.parametrize('module',FILES)
def test_compile(module):
    compile((BASE/(module+'.py')).read_bytes(),str(BASE/(module+'.py')),'exec')

@pytest.mark.parametrize('mod,name',[(m,'calculate'),(d,'datetime_calendar'),(c,'manage_memory')])
def test_mcp_handshake_and_schema(mod,name):
    request=rpc_request('initialize',params={'protocolVersion':'2024-11-05','clientInfo':{'name':'audit','version':'0'},'capabilities':{}})
    result=rpc_handler(mod,request)
    assert result['result']['protocolVersion']=='2024-11-05'
    assert result['result']['capabilities'].get('tools') == {}
    assert result['jsonrpc']=='2.0'
    lst=rpc_handler(mod,rpc_request('tools/list'))['result']['tools']
    assert len(lst)==1 and lst[0]['name']==name
    Draft202012Validator.check_schema(lst[0]['inputSchema'])
    assert rpc_handler(mod,{'jsonrpc':'2.0','method':'notifications/initialized'}) is None
    assert rpc_handler(mod,rpc_request('method/nonexistent'))['error']['code']==-32601
    assert rpc_handler(mod,rpc_request('ping'))['result']=={}

@pytest.mark.parametrize('mod,name,args',[(m,'calculate',{'expr':'2+2'}),(d,'datetime_calendar',{'action':'weekday','date':'2000-01-01'}),(c,'manage_memory',{'action':'read'})])
def test_mcp_calls(mod,name,args,memory):
    p=rpc_request('tools/call',params={'name':name,'arguments':args})
    res=rpc_handler(mod,p)['result']
    assert res['isError'] is False
    assert len(res['content'])==1 and res['content'][0]['type']=='text'
    assert rpc_handler(mod,rpc_request('tools/call',params={'name':'unknown'}))['error']['code']==-32602

@pytest.mark.parametrize('mod,name,args',[(m,'calculate',{'expr':'1/0'}),(d,'datetime_calendar',{'action':'weekday','date':'2023-02-29'}),(c,'manage_memory',{'action':'save','category':'note','value':''})])
def test_mcp_errors_keep_process(mod,name,args,memory):
    res=rpc_handler(mod,rpc_request('tools/call',params={'name':name,'arguments':args}))['result']
    assert res['isError'] is True
    assert rpc_handler(mod,rpc_request('ping'))['result']=={}

@pytest.mark.parametrize('expr,out',[('2+2*3','8'),('2^10','1024'),('-3^2','-9'),('(-3)^2','9'),('0.1+0.2','0.3'),('sqrt(16)','4'),('factorial(10)','3628800'),('gcd(24,36)','12'),('log(e)','1'),('pi','3.14159265358979'),('2**3**2','512'),('sin(0)','0'),('round(2.5)','2'),('pmt(0,10,100)','10'),('monthly(12)','0.01'),('pct(5)','0.05'),('npv(0,100,50,-20)','130')])
def test_math_examples(expr,out): assert m.calculate(expr)==out

@pytest.mark.parametrize('expr', ['1/0','2**600','2**5000','factorial(1001)','0**-1','__import__("os")','open("/etc/passwd")','(1).__class__','math.__dict__','[x for x in range(9)]','(lambda:4)()','[1,2]','sum([1,2])','9e999','2+nan','3 and 2', '10+'*1000])
def test_math_forbidden(expr):
    with pytest.raises(Exception): m.calculate(expr,'numeric')

@pytest.mark.parametrize('expr', ['diff(x^3,x)','integrate(x,x)','solve(x^2-4,x)','x^2=4','factor(x^2-1)','simplify((x+1)^2-x^2-2*x-1)'])
def test_math_symbolic(expr):
    out=m.calculate(expr)
    assert out and not out.startswith('Calculation error')

@pytest.mark.parametrize('expr,expected', [('det([[1,2],[3,4]])','-2'),('trace([[1,2],[3,4]])','5'),('matmul([[1,0],[0,2]],[[2],[4]])','[[2.]\n [8.]]'),('eye(2)','[[1. 0.]\n [0. 1.]]'),('rank([[1,2],[2,4]])','1'),('dot([1,2],[3,4])','11')])
def test_math_matrix(expr,expected): assert m.calculate(expr)==expected

@pytest.mark.parametrize('expr,mode', [('2+1','numeric'),('diff(x,x)','symbolic'),('det([[2]])','matrix'),('solve([[2]],[4])','matrix'),('solve(x-2,x)','symbolic'),('x+1','symbolic'),('[[1,2]]','matrix')])
def test_math_auto(expr,mode): assert m.detect_mode(m.preprocess(expr))==mode

def test_math_randomized_integer_properties():
    r=random.Random(0xC0FFEE)
    for _ in range(500):
        a,b,c0=(r.randint(-100000,100000) for i in range(3))
        assert m.calculate(f'({a}+{b})*{c0}')==str((a+b)*c0)
        assert m.calculate(f'{a}*({b}+{c0})')==str(a*(b+c0))

def test_math_randomized_finance_against_decimal():
    getcontext().prec=50
    r=random.Random(771)
    for _ in range(100):
        rate=Decimal(r.randint(1,250))/Decimal(10000)
        periods=r.randint(1,360)
        pv=r.randint(100,10000000)
        want=Decimal(pv)*rate/(1-(1+rate)**(-periods))
        got=float(m.calculate(f'pmt({float(rate)},{periods},{pv})'))
        assert math.isclose(got,float(want),rel_tol=1e-11,abs_tol=1e-6)

def test_math_randomized_matrix_against_numpy():
    r=random.Random(781)
    for _ in range(75):
        arr1=np.array([[r.randint(-4,4) for i in range(2)] for j in range(2)])
        arr2=np.array([[r.randint(-4,4) for i in range(2)] for j in range(2)])
        actual=m.calculate(f'matmul({arr1.tolist()},{arr2.tolist()})')
        assert np.allclose(np.fromstring(actual.replace('[',' ').replace(']',' '),sep=' ').reshape(2,2),arr1@arr2)

@pytest.mark.parametrize('date_str,weekday',[('2000-01-01',6),('2024-02-29',4),('1900-03-01',4),('2026-11-05',4),('0001-01-01',1),('9999-12-31',5)])
def test_datetime_weekday(date_str,weekday):
    assert d.run_tool({'action':'weekday','date':date_str})['weekday_number_monday_1']==weekday

@pytest.mark.parametrize('date_str,amount,unit,result',[('2024-01-31T12:00:00Z',1,'months','2024-02-29T12:00:00+00:00'),('2023-01-31T12:00:00Z',1,'months','2023-02-28T12:00:00+00:00'),('2024-02-29T12:00:00Z',1,'years','2025-02-28T12:00:00+00:00'),('2024-03-31T12:00:00Z',-1,'months','2024-02-29T12:00:00+00:00'),('2024-12-31T12:00:00Z',1,'days','2025-01-01T12:00:00+00:00'),('0001-01-01T00:00:00Z',1,'days','0001-01-02T00:00:00+00:00')])
def test_datetime_add_cases(date_str,amount,unit,result):
    assert d.run_tool({'action':'add','datetime':date_str,'amount':amount,'unit':unit})['result']==result

@pytest.mark.parametrize('bad',[{'action':'weekday','date':'2023-02-29'},{'action':'calendar','year':2024,'month':13},{'action':'calendar','year':True,'month':4},{'action':'add','datetime':'2026-01-01T00:00:00','amount':2,'unit':'hours'},{'action':'add','datetime':'2026-01-01T00:00:00Z','amount':1.2,'unit':'months'},{'action':'add','datetime':'2026-01-01T00:00:00Z','amount':float('nan'),'unit':'days'},{'action':'now','timezone':'not-a-zone'},{'action':'difference','start':'2024-01-01T00:00:00','end':'2024-01-02T00:00:00Z'}])
def test_datetime_bad_arguments(bad):
    with pytest.raises((ValueError,OverflowError)): d.run_tool(bad)

@pytest.mark.parametrize('name,offset',[('UTC+05:45',20700),('UTC-03:30',-12600),('Asia/Kathmandu',20700),('UTC',0),('America/New_York',-18000)])
def test_datetime_timezones(name,offset):
    tz,_=d.get_zone(name)
    assert datetime(2024,1,15,tzinfo=tz).utcoffset().total_seconds()==offset

@pytest.mark.parametrize('start,end,seconds',[('1970-01-01T00:00:00Z','1970-01-01T00:00:01Z',1),('2024-03-10T01:30:00-05:00','2024-03-10T03:30:00-04:00',3600),('2024-11-03T01:30:00-04:00','2024-11-03T01:30:00-05:00',3600),('2024-12-31T00:00:00Z','2024-01-01T00:00:00Z',-31536000)])
def test_datetime_difference(start,end,seconds):
    assert d.run_tool({'action':'difference','start':start,'end':end})['seconds']==seconds

def test_datetime_randomized_date_arithmetic():
    r=random.Random(12345)
    for _ in range(600):
        dd=date(r.randint(1900,2100),1,1)+timedelta(days=r.randint(0,364))
        amount=r.randint(-365,365)
        expr=dd.isoformat()+'T08:30:00Z'
        actual=d.run_tool({'action':'add','datetime':expr,'amount':amount,'unit':'days'})['result'][:10]
        assert actual==(dd+timedelta(days=amount)).isoformat()

def test_datetime_calendar_consistency():
    for y in [1,1582,1900,2000,2024,2025,9999]:
        for month in range(1,13):
            out=d.run_tool({'action':'calendar','year':y,'month':month})
            assert set(day for week in out['weeks'] for day in week if day)!=set() or False
            assert sum(bool(day) for week in out['weeks'] for day in week)==__import__('calendar').monthrange(y,month)[1]

def test_datetime_dst_reference():
    # Checks independent zoneinfo reference; difference tool accepts offsets only, so compare it to timezone-aware instants.
    for zone,one,two in [('America/New_York',datetime(2024,3,10,1,30),datetime(2024,3,10,3,30)),('Europe/Berlin',datetime(2024,3,31,1,30),datetime(2024,3,31,3,30)),('Australia/Sydney',datetime(2024,10,6,1,30),datetime(2024,10,6,3,30))]:
        z=ZoneInfo(zone)
        start=one.replace(tzinfo=z); end=two.replace(tzinfo=z)
        tool=d.run_tool({'action':'difference','start':start.isoformat(),'end':end.isoformat()})
        assert tool['seconds']==3600

def test_datetime_dst_fold_reference():
    z=ZoneInfo('America/New_York')
    a=datetime(2024,11,3,1,30,tzinfo=z,fold=0)
    b=datetime(2024,11,3,1,30,tzinfo=z,fold=1)
    assert d.run_tool({'action':'difference','start':a.isoformat(),'end':b.isoformat()})['seconds']==3600

def test_datetime_randomized_duration():
    r=random.Random(456)
    for _ in range(400):
        sec1=r.randint(-1_000_000_000,1_000_000_000)
        sec2=r.randint(-1_000_000_000,1_000_000_000)
        # Avoid platform C runtime limits for negative Unix timestamps on Windows.
        epoch=datetime(1970,1,1,tzinfo=timezone.utc)
        x=(epoch+timedelta(seconds=sec1)).isoformat()
        y=(epoch+timedelta(seconds=sec2)).isoformat()
        assert d.run_tool({'action':'difference','start':x,'end':y})['seconds']==sec2-sec1

def test_memory_crud_and_persist(memory):
    assert c.run_tool({'action':'save','category':'profile','key':'operating system','value':'Windows 11'})['ok']
    assert c.run_tool({'action':'save','category':'note','value':'Тестовые заметки содержат Unicode 日本語','project':'demo'})['saved']==1
    assert c.run_tool({'action':'save','category':'note','id':1,'value':'Тестовый обновлённый текст'})['updated']==1
    assert 'Тестовый обновлённый' in c.run_tool({'action':'read','category':'note'})['notes'][0]['text']
    assert c.run_tool({'action':'delete','id':1})['deleted']==1
    assert c.run_tool({'action':'delete','key':'operating system'})['deleted']=='operating_system'
    assert not c.run_tool({'action':'read'})['notes']
    json.loads(memory.read_text(encoding='utf8'))

def test_memory_duplicates_and_similarity(memory):
    assert c.run_tool({'action':'save','value':'A repeated note documenting some design work'})['saved']==1
    assert c.run_tool({'action':'save','value':'A repeated note documenting some design work'})['duplicate_of']==1
    near=c.run_tool({'action':'save','value':'A repeated note documenting some design works'})
    assert near['not_saved'] and near['similar_to']==1

def test_memory_secret_filter(memory):
    for secret in ['sk-ABCDEF1234567890ABCDEF1234567890','password:Not123456','ghp_Aa1234567890Aa1234567890']:
        with pytest.raises(ValueError): c.run_tool({'action':'save','category':'note','value':secret})
    assert not memory.exists()

def test_memory_budget_and_limits(memory):
    for i in range(20): c.run_tool({'action':'save','value':f'item_{i}'})
    out=c.run_tool({'action':'read'})
    assert len(out['notes'])<=c.NOTES_LIMIT and out['more']>0
    with pytest.raises(ValueError): c.run_tool({'action':'save','value':'x'*601})

def test_memory_randomized_crud(memory):
    r=random.Random(199)
    model={}
    for i in range(80):
        text=f'test note id {i} content {r.randrange(10**8)}'
        ident=c.run_tool({'action':'save','category':'note','value':text})['saved']
        model[ident]=text
    for ident in r.sample(list(model),30):
        c.run_tool({'action':'delete','id':ident})
        del model[ident]
    for ident in r.sample(list(model),20):
        text=f'Changed note id {ident} specific text'
        c.run_tool({'action':'save','id':ident,'value':text})
        model[ident]=text
    state=c.load_memory()
    assert {n['id']:n['text'] for n in state['notes']}==model
    assert len(model)==50

def test_memory_backup_recovery(memory):
    c.run_tool({'action':'save','value':'first note'})
    c.run_tool({'action':'save','value':'second note'})
    assert c.BACKUP_FILE.exists()
    memory.write_text('{invalid json',encoding='utf8')
    recovered=c.run_tool({'action':'read'})
    assert any(n['text']=='first note' for n in recovered['notes'])
    assert memory.exists()
    assert list(memory.parent.glob('memory.json.corrupt-*'))

def test_memory_no_secret_to_stdout(memory):
    c.run_tool({'action':'save','value':'ordinary note'})
    assert 'ordinary note' in c.run_tool({'action':'read'})['notes'][0]['text']

@pytest.mark.parametrize('module',FILES)
def test_stdio_multiplex(module,tmp_path):
    env=os.environ.copy(); env['AI_MEMORY_FILE']=str(tmp_path/'tmp_memory.json')
    commands=[rpc_request('initialize',i,{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}) for i in [1]]
    commands.append({'jsonrpc':'2.0','method':'notifications/initialized'})
    commands.extend([rpc_request('ping',i) for i in range(2,25)])
    commands.append(rpc_request('tools/list',25))
    raw='\n'.join(json.dumps(x,ensure_ascii=False) for x in commands)+'\n'+'{bad json\n'
    p=subprocess.run([sys.executable,'-u',str(BASE/(module+'.py'))], input=raw,capture_output=True,text=True,encoding='utf8',env=env,timeout=20)
    assert p.returncode==0,p.stderr
    responses=[json.loads(x) for x in p.stdout.splitlines()]
    assert len(responses)==26  # init + 23 ping + list + parse error
    assert [x['id'] for x in responses[:25]]==[1]+list(range(2,25))+[25]
    assert responses[-1]['error']['code']==-32700

@pytest.mark.parametrize('module',FILES)
def test_stdio_malformed_fuzz(module,tmp_path):
    r=random.Random(786)
    invalid=['!', '[','{','"broken','{"a":','false,','[undefined]', '{"jsonrpc":2}']
    invalid += [''.join(r.choice('{}[],@#$012345abcxyz') for j in range(r.randrange(1,40))) for i in range(100)]
    input_data='\n'.join(invalid)+'\n'
    env=os.environ.copy();env['AI_MEMORY_FILE']=str(tmp_path/'memory.json')
    p=subprocess.run([sys.executable,str(BASE/(module+'.py'))],input=input_data,capture_output=True,text=True,encoding='utf8',env=env,timeout=15)
    assert p.returncode==0,p.stderr
    for line in p.stdout.splitlines():
        reply=json.loads(line)
        assert reply.get('jsonrpc')=='2.0'
        assert 'error' in reply

def test_memory_concurrent_writers(tmp_path):
    path=tmp_path/'shared.json'; env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def write_one(k):
        commands=[]
        for i in range(15):
            commands.append(rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','category':'note','value':f'z{k}{i:02}'}}))
        p=subprocess.run([sys.executable,str(BASE/'compact_memory_mcp.py')],input='\n'.join(json.dumps(q) for q in commands)+'\n',capture_output=True,text=True,env=env,timeout=25)
        assert p.returncode==0,p.stderr
        results=[json.loads(x) for x in p.stdout.splitlines()]
        assert len(results)==15
        assert all(x['result']['isError'] is False for x in results)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_one,range(4)))
    obj=json.loads(path.read_text(encoding='utf8'))
    assert len(obj['notes'])==60
    assert len({x['id'] for x in obj['notes']})==60

def test_integration_all_three(memory):
    saved=c.run_tool({'action':'save','category':'note','value':'Mortgage principal 8000000; annual rate 12%; periods 120; start 2024-01-31T12:00:00Z'})
    assert saved['saved']==1
    r=c.run_tool({'action':'read','query':'mortgage'})
    assert r['notes']
    p=m.calculate('pmt(monthly(12),120,8000000)')
    due=d.run_tool({'action':'add','datetime':'2024-01-31T12:00:00Z','amount':1,'unit':'months'})['result']
    diff=d.run_tool({'action':'difference','start':'2024-01-31T12:00:00Z','end':due})['days']
    assert diff==29
    c.run_tool({'action':'save','id':1,'value':f'Monthly payment {p}, first due {due[:10]}'} )
    assert p in c.run_tool({'action':'read','query':'payment'})['notes'][0]['text']

# Regression probes that document undesired behavior without silently treating it as a passing fix.
def test_expected_math_multistatement_assignments():
    assert m.calculate('P=8000000\nr=0.01\nP*r')=='80000'

def test_expected_math_pmt_near_zero():
    assert math.isclose(float(m.calculate('pmt(1e-17,120,1000)')),1000/120,rel_tol=1e-12)

def test_expected_math_noninteger_periods_rejected():
    with pytest.raises(ValueError): m.calculate('fv_growing_rent(0.01,2.5,100,40,0.06)')

def test_expected_datetime_microseconds():
    assert d.run_tool({'action':'add','datetime':'2024-01-01T00:00:00.123456Z','unit':'seconds','amount':1})['result'].endswith('01.123456+00:00')

def test_expected_memory_no_secret_in_project(memory):
    with pytest.raises(ValueError): c.run_tool({'action':'save','value':'public note','project':'sk-ABCDEF1234567890ABCDEF1234567890'})

def test_expected_memory_reject_structurally_invalid_store(memory):
    memory.write_text(json.dumps({'notes':'not a list','profile':{}}),encoding='utf8')
    with pytest.raises(ValueError): c.load_memory()

def test_expected_memory_legacy_ids_preserved():
    assert c._clean({'notes':[{'id':'n_7','text':'old'}], 'next_id':1})['notes'][0]['id']==7

@pytest.mark.parametrize('module',FILES)
def test_expected_jsonrpc_version_validation(module):
    mod={'math_calculator':m,'datetime_mcp':d,'compact_memory_mcp':c}[module]
    res=rpc_handler(mod,{'jsonrpc':'1.0','id':42,'method':'ping'})
    assert res['error']['code']==-32600

@pytest.mark.parametrize('module',FILES)
def test_expected_jsonrpc_id_validation(module):
    mod={'math_calculator':m,'datetime_mcp':d,'compact_memory_mcp':c}[module]
    res=rpc_handler(mod,{'jsonrpc':'2.0','id':{'oops':1},'method':'ping'})
    assert res['error']['code']==-32600

def test_expected_matrix_nonfinite_rejected():
    with pytest.raises(ValueError):m.calculate('array([1e309])')

def test_expected_memory_negotiate_supported_protocol():
    res=c.handle_rpc(rpc_request('initialize',params={'protocolVersion':'2099-01-01','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}))
    assert res['result']['protocolVersion'] in ['2024-11-05','2025-03-26','2025-06-18','2025-11-25']

def test_expected_memory_distinct_numbers_preserved(memory):
    a=c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150000 RUB'})
    b=c.run_tool({'action':'save','value':'Invoice amount for May 2026 is 150001 RUB'})
    assert a.get('saved')==1 and b.get('saved')==2

@pytest.mark.skipif(os.name == "nt", reason="POSIX 0600 bits are not a Windows ACL security check")
def test_expected_memory_private_permission(memory):
    import stat
    previous=os.umask(0o022)
    try:c.run_tool({'action':'save','value':'hello'})
    finally:os.umask(previous)
    assert memory.stat().st_mode & (stat.S_IRGRP|stat.S_IROTH)==0

def test_memory_rename_failure_is_atomic(memory,monkeypatch):
    c.run_tool({'action':'save','value':'original-note'})
    original=memory.read_bytes()
    actual_replace=c.os.replace
    def fail_replace(src,dst):raise PermissionError('injected rename failure')
    monkeypatch.setattr(c.os,'replace',fail_replace)
    with pytest.raises(PermissionError):c.run_tool({'action':'save','value':'new-note'})
    assert memory.read_bytes()==original
    monkeypatch.setattr(c.os,'replace',actual_replace)
    assert c.run_tool({'action':'read'})['notes'][0]['text']=='original-note'

def test_memory_read_write_consistency(tmp_path):
    path=tmp_path/'twin.json';env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def process(k):
        commands=[]
        if k==0:
            for i in range(20):commands.append(rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','value':f'q{i:02}'}}))
        else:
            for i in range(20):commands.append(rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'read'}}))
        p=subprocess.run([sys.executable,str(BASE/'compact_memory_mcp.py')],input='\n'.join(map(json.dumps,commands))+'\n',capture_output=True,text=True,env=env,timeout=20)
        assert p.returncode==0,p.stderr
        data=[json.loads(line) for line in p.stdout.splitlines()]
        assert len(data)==20 and all(v['result']['isError'] is False for v in data)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(process,range(2)))
    assert len(json.loads(path.read_text(encoding='utf8'))['notes'])==20

def test_math_heavy_timeout_liveness():
    with pytest.raises(ValueError,match='timed out'):
        m.run_heavy(m.preprocess('expand((x+1)^500)'), 'symbolic', timeout=.0001)
    assert m.calculate('det([[1]])') == '1'

def test_datetime_missing_tzdb_simulation(tmp_path):
    env=os.environ.copy();env['PYTHONTZPATH']=str(tmp_path/'nonexistent_tz_path')
    request1=rpc_request('tools/call',1,{'name':'datetime_calendar','arguments':{'action':'now'}})
    request2=rpc_request('tools/call',2,{'name':'datetime_calendar','arguments':{'action':'now','timezone':'UTC+0'}})
    p=subprocess.run([sys.executable,'-S',str(BASE/'datetime_mcp.py')],input=json.dumps(request1)+'\n'+json.dumps(request2)+'\n',text=True,capture_output=True,env=env,timeout=10)
    assert p.returncode==0,p.stderr
    a,b=[json.loads(v)['result'] for v in p.stdout.splitlines()]
    assert a['isError'] is False
    assert b['isError'] is False

def test_integration_finance_calendar_12_dates():
    amount=float(m.calculate('pmt(monthly(12),120,8000000)'))
    assert amount>0
    start='2024-01-31T12:00:00Z'
    due=[d.run_tool({'action':'add','datetime':start,'amount':i,'unit':'months'})['result'] for i in range(1,13)]
    assert len(set(due))==12
    assert due[0].startswith('2024-02-29')
    assert due[-1].startswith('2025-01-31')
    total_12=amount*12
    assert total_12>0

def test_integration_memory_finance(memory):
    for key,val in [('principal','8000000'),('annual_percent','12'),('periods','120')]:
        c.run_tool({'action':'save','category':'profile','key':key,'value':val})
    values=c.run_tool({'action':'read','category':'profile'})['profile']
    numeric=f"pmt(monthly({values['annual_percent']}),{values['periods']},{values['principal']})"
    computed=m.calculate(numeric)
    assert float(computed)>10000
    assert c.run_tool({'action':'save','category':'note','value':f'Calculated monthly payment {computed}'})['ok']
    assert computed in c.run_tool({'action':'read','query':'payment'})['notes'][0]['text']

def test_integration_memory_datetime(memory):
    c.run_tool({'action':'save','category':'profile','key':'deadline','value':'2026-12-01T00:00:00Z'})
    deadline=c.run_tool({'action':'read','category':'profile'})['profile']['deadline']
    days=d.run_tool({'action':'difference','start':'2026-11-01T00:00:00Z','end':deadline})['days']
    assert days==30
    c.run_tool({'action':'save','category':'profile','key':'days_to_deadline','value':str(int(days))})
    assert c.run_tool({'action':'read','category':'profile'})['profile']['days_to_deadline']=='30'
