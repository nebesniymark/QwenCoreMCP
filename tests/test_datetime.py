"""Datetime regression and integration tests (consolidated)."""
from ._support import (
    ZoneInfo,
    audit_BASE,
    audit_c,
    audit_d,
    audit_m,
    audit_rpc_request,
    date,
    datetime,
    json,
    os,
    pytest,
    random,
    regression_d,
    regression_legacy,
    release_BASE,
    release_d,
    release_rpc,
    subprocess,
    sys,
    time_engine,
    timedelta,
    timezone,
)


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
    p=subprocess.run([sys.executable,'-S',str(release_BASE/'time_engine.py')],check=False, input=json.dumps(payload)+'\n',text=True,capture_output=True,env=env,timeout=10)
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

def test_release_datetime_iana_wall_vs_elapsed_dst():
    base={'action':'add','datetime':'2024-03-09T12:00:00-05:00',
          'timezone':'America/New_York'}
    assert release_d.run_tool(dict(base,unit='days',amount=1))['result']=='2024-03-10T12:00:00-04:00'
    assert release_d.run_tool(dict(base,unit='hours',amount=24))['result']=='2024-03-10T13:00:00-04:00'

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
            assert {day for week in out['weeks'] for day in week if day} != set()
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

def test_audit_expected_datetime_microseconds():
    assert audit_d.run_tool({'action':'add','datetime':'2024-01-01T00:00:00.123456Z','unit':'seconds','amount':1})['result'].endswith('01.123456+00:00')

def test_audit_datetime_missing_tzdb_simulation(tmp_path):
    env=os.environ.copy();env['PYTHONTZPATH']=str(tmp_path/'nonexistent_tz_path')
    request1=audit_rpc_request('tools/call',1,{'name':'datetime_calendar','arguments':{'action':'now'}})
    request2=audit_rpc_request('tools/call',2,{'name':'datetime_calendar','arguments':{'action':'now','timezone':'UTC+0'}})
    p=subprocess.run([sys.executable,'-S',str(audit_BASE/'time_engine.py')],check=False, input=json.dumps(request1)+'\n'+json.dumps(request2)+'\n',text=True,capture_output=True,env=env,timeout=10)
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

def test_audit_integration_memory_datetime(audit_memory):
    audit_c.run_tool({'action':'save','category':'profile','key':'deadline','value':'2026-12-01T00:00:00Z'})
    deadline=audit_c.run_tool({'action':'read','category':'profile'})['profile']['deadline']
    days=audit_d.run_tool({'action':'difference','start':'2026-11-01T00:00:00Z','end':deadline})['days']
    assert days==30
    audit_c.run_tool({'action':'save','category':'profile','key':'days_to_deadline','value':str(int(days))})
    assert audit_c.run_tool({'action':'read','category':'profile'})['profile']['days_to_deadline']=='30'

def test_regression_datetime_timezone_description_lists_actions():
    result = regression_d.handle(regression_legacy('tools/list',params={}))['result']
    desc = result['tools'][0]['inputSchema']['properties']['timezone']['description']
    for action in ('convert','localize','timestamp','add','now'):
        assert action in desc

def test_regression_datetime_unknown_action_lists_supported_actions():
    with pytest.raises(ValueError, match='convert, timestamp, or localize'):
        regression_d.run_tool({'action':'not_real'})

@pytest.mark.parametrize('unit', ['days', 'weeks', 'months', 'years'])
def test_dst_add_zero_preserves_instant(unit):
    answer = time_engine.run_tool({"action": "add", "datetime": "2024-11-03T01:30:00-05:00",
                                   "timezone": "America/New_York", "unit": unit, "amount": 0})
    assert answer["result"] == "2024-11-03T01:30:00-05:00"
