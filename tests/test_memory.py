"""Memory regression and integration tests (consolidated)."""
from ._support import (
    Path,
    ThreadPoolExecutor,
    audit_BASE,
    audit_c,
    audit_m,
    audit_rpc_request,
    json,
    memory,
    memory_engine,
    nullcontext,
    os,
    pytest,
    random,
    regression_BASE,
    regression_c,
    regression_legacy,
    regression_load,
    release_BASE,
    release_c,
    release_rpc,
    stat,
    subprocess,
    sys,
)


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

def test_release_memory_reject_long_project(release_memory):
    with pytest.raises(ValueError):release_c.run_tool({'action':'save','value':'abc','project':'a'*61})

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
        p=subprocess.run([sys.executable,str(release_BASE/'memory_engine.py')],check=False, input='\n'.join(json.dumps(x) for x in requests)+'\n',text=True,capture_output=True,env=env,timeout=20)
        assert p.returncode==0,p.stderr
        assert all(x['result']['isError'] is False for x in map(json.loads,p.stdout.splitlines()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer,range(4)))
    entries=json.loads(path.read_text())['notes']
    assert len(entries)==48 and len({n['id'] for n in entries})==48

def test_release_memory_old_hex_ids():
    assert release_c._clean({'notes':[{'id':'n_a1b2c3','text':'old memo'}]})['notes'][0]['id']==0xa1b2c3
    assert release_c.arg_id({'id':'n_a1b2c3'})==0xa1b2c3

def test_release_memory_unknown_nonempty_json_rejected():
    with pytest.raises(ValueError,match='missing profile'):
        release_c._clean({'injected':'nonsense'})

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

def test_audit_memory_concurrent_writers(tmp_path):
    path=tmp_path/'shared.json'; env=os.environ.copy();env['AI_MEMORY_FILE']=str(path)
    def write_one(k):
        commands=[]
        for i in range(15):
            commands.append(audit_rpc_request('tools/call',i,{'name':'manage_memory','arguments':{'action':'save','category':'note','value':f'z{k}{i:02}'}}))
        p=subprocess.run([sys.executable,str(audit_BASE/'memory_engine.py')],check=False, input='\n'.join(json.dumps(q) for q in commands)+'\n',capture_output=True,text=True,env=env,timeout=25)
        assert p.returncode==0,p.stderr
        results=[json.loads(x) for x in p.stdout.splitlines()]
        assert len(results)==15
        assert all(x['result']['isError'] is False for x in results)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_one,range(4)))
    obj=json.loads(path.read_text(encoding='utf8'))
    assert len(obj['notes'])==60
    assert len({x['id'] for x in obj['notes']})==60

def test_audit_expected_memory_reject_structurally_invalid_store(audit_memory):
    audit_memory.write_text(json.dumps({'notes':'not a list','profile':{}}),encoding='utf8')
    with pytest.raises(ValueError): audit_c.load_memory()

def test_audit_expected_memory_legacy_ids_preserved():
    assert audit_c._clean({'notes':[{'id':'n_7','text':'old'}], 'next_id':1})['notes'][0]['id']==7

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
        p=subprocess.run([sys.executable,str(audit_BASE/'memory_engine.py')],check=False, input='\n'.join(map(json.dumps,commands))+'\n',capture_output=True,text=True,env=env,timeout=20)
        assert p.returncode==0,p.stderr
        data=[json.loads(line) for line in p.stdout.splitlines()]
        assert len(data)==20 and all(v['result']['isError'] is False for v in data)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(process,range(2)))
    assert len(json.loads(path.read_text(encoding='utf8'))['notes'])==20

def test_audit_integration_memory_finance(audit_memory):
    for key,val in [('principal','8000000'),('annual_percent','12'),('periods','120')]:
        audit_c.run_tool({'action':'save','category':'profile','key':key,'value':val})
    values=audit_c.run_tool({'action':'read','category':'profile'})['profile']
    numeric=f"pmt(monthly({values['annual_percent']}),{values['periods']},{values['principal']})"
    computed=audit_m.calculate(numeric)
    assert float(computed)>10000
    assert audit_c.run_tool({'action':'save','category':'note','value':f'Calculated monthly payment {computed}'})['ok']
    assert computed in audit_c.run_tool({'action':'read','query':'payment'})['notes'][0]['text']

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
    fresh = regression_load('memory_engine')
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
        proc = subprocess.run([sys.executable, str(regression_BASE / 'memory_engine.py')], check=False,
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
    fresh = regression_load('memory_engine')
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

def test_read_note_id_returns_complete_text_without_truncation() -> None:
    text = ("Project planning note: reviewed modules and tested changes. " * 11)[:memory_engine.MAX_NOTE]
    state = {"profile": {}, "notes": [{"id": 4, "text": text, "project": "build",
                                         "updated_at": "2026-11-07"}], "next_id": 5}
    result, changed = memory_engine.tool_read({"action": "read", "category": "note", "id": 4}, state)
    assert result == {"notes": [{"id": 4, "text": text, "project": "build"}]}
    assert not changed
    preview, _ = memory_engine.tool_read({"action": "read", "category": "note"}, state)
    assert len(preview["notes"][0]["text"]) < len(text)

def test_recovery_warning_is_shown_once_per_marker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The recovery marker cache uses (absolute_path, mtime_ns) tuples."""
    marker = tmp_path / "recovery-marker"
    marker.write_text("synthetic marker", encoding="utf-8")
    monkeypatch.setattr(memory, "file_lock", nullcontext)
    monkeypatch.setattr(memory, "load_memory", lambda: {"notes": [], "profile": {}})
    monkeypatch.setattr(memory, "_recovery_warning", lambda: "Recovery notice")
    monkeypatch.setattr(memory, "_recovery_notice_path", lambda: marker)
    monkeypatch.setattr(memory, "tool_read", lambda args, state: ({"notes": []}, False))
    monkeypatch.setattr(memory, "_SHOWN_RECOVERY_NOTICES", set())

    first = memory.run_tool({"action": "read"})
    second = memory.run_tool({"action": "read"})

    assert first["warning"] == "Recovery notice"
    assert "warning" not in second
    assert memory._SHOWN_RECOVERY_NOTICES == {(str(marker.absolute()), marker.stat().st_mtime_ns)}
