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

BASE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('math_calculator')
d = load('datetime_mcp')
c = load('compact_memory_mcp')
SERVERS = [(m, m.handle, 'calculate'), (d, d.handle, 'datetime_calendar'),
           (c, c.handle_rpc, 'manage_memory')]


@pytest.fixture
def memory(tmp_path, monkeypatch):
    f = tmp_path / 'state.json'
    monkeypatch.setattr(c, 'MEMORY_FILE', f)
    monkeypatch.setattr(c, 'BACKUP_FILE', f.with_name(f.name + '.bak'))
    monkeypatch.setattr(c, 'LOCK_FILE', f.with_name(f.name + '.lock'))
    monkeypatch.setattr(c, 'LEGACY_FILE', tmp_path / 'legacy.json')
    return f


def modern(method, *, args=None, version='2026-07-28', id_=3):
    params = {'_meta': {'io.modelcontextprotocol/protocolVersion': version,
                       'io.modelcontextprotocol/clientCapabilities': {}}}
    if args:
        params.update(args)
    return {'jsonrpc': '2.0', 'id': id_, 'method': method, 'params': params}


def legacy(method, *, params=None):
    return {'jsonrpc': '2.0', 'id': 3, 'method': method, 'params': params}


@pytest.mark.parametrize('module,handle,tool_name', SERVERS)
def test_modern_discovery_result_required_fields(module, handle, tool_name, memory):
    result = handle(modern('server/discover'))['result']
    assert result['resultType'] == 'complete'
    assert result['supportedVersions'] == ['2026-07-28']
    assert result['capabilities'] == {'tools': {}}
    assert result['ttlMs'] == 0 and result['cacheScope'] == 'private'
    assert result['_meta']['io.modelcontextprotocol/serverInfo']['name'] == module.SERVER_NAME


@pytest.mark.parametrize('module,handle,tool_name', SERVERS)
def test_modern_list_cache_schema(module, handle, tool_name, memory):
    result = handle(modern('tools/list'))['result']
    assert result['resultType'] == 'complete'
    assert result['ttlMs'] == 0 and result['cacheScope'] == 'private'
    assert len(result['tools']) == 1 and result['tools'][0]['name'] == tool_name
    legacy_res = handle(legacy('tools/list'))['result']
    assert all(key not in legacy_res for key in ('resultType', 'ttlMs', 'cacheScope'))


@pytest.mark.parametrize('module,handle,tool_name', SERVERS)
def test_modern_call_has_result_type(module, handle, tool_name, memory):
    args = ({'expr': '2+2'} if tool_name == 'calculate' else
            {'action': 'now'} if tool_name == 'datetime_calendar' else
            {'action': 'read'})
    result = handle(modern('tools/call', args={'name': tool_name, 'arguments': args}))['result']
    assert result['resultType'] == 'complete'
    assert isinstance(result['content'], list)
    assert not result['isError']
    assert 'ttlMs' not in result and 'cacheScope' not in result


@pytest.mark.parametrize('module,handle,tool_name', SERVERS)
def test_version_error_has_structured_data(module, handle, tool_name, memory):
    error = handle(modern('tools/list', version='2099-01-01'))['error']
    assert error['code'] == -32022
    assert '2026-07-28' in error['data']['supported']
    assert '2025-11-25' in error['data']['supported']
    assert error['data']['requested'] == '2099-01-01'


def test_memory_recovery_is_explicit_and_persistent(memory, monkeypatch):
    c.run_tool({'action': 'save', 'value': 'note one'})
    c.run_tool({'action': 'save', 'value': 'note two'})
    raw = json.loads(memory.read_text(encoding='utf-8'))
    raw['notes'][1]['created_at'] = 123  # logically invalid, valid JSON
    memory.write_text(json.dumps(raw), encoding='utf-8')
    res = c.run_tool({'action': 'read'})
    assert 'warning' in res and 'restored from a backup' in res['warning']
    assert 'note one' in str(res)
    assert 'note two' not in str(res)
    quarantined = list(memory.parent.glob(memory.name + '.corrupt-*'))
    assert len(quarantined) == 1
    assert 'note two' in quarantined[0].read_text(encoding='utf-8')
    res2 = c.run_tool({'action': 'save', 'value': 'note three'})
    assert 'warning' not in res2  # one notice per server process
    assert 'warning' not in c.run_tool({'action': 'read'})
    # Emulates new Python process importing module again.
    fresh = load('compact_memory_mcp')
    monkeypatch.setattr(fresh, 'MEMORY_FILE', memory)
    monkeypatch.setattr(fresh, 'BACKUP_FILE', c.BACKUP_FILE)
    monkeypatch.setattr(fresh, 'LOCK_FILE', c.LOCK_FILE)
    assert 'warning' in fresh.run_tool({'action': 'read'})
    assert c._recovery_notice_path().exists()


def test_memory_cannot_resurrect_legacy_after_quarantine(tmp_path):
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
    reqs = [legacy('tools/call', params={'name':'manage_memory','arguments':{'action':'read'}})]

    def send(*items):
        proc = subprocess.run([sys.executable, str(BASE / 'compact_memory_mcp.py')],
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


def test_memory_does_not_start_empty_over_orphaned_backup(memory):
    c.BACKUP_FILE.write_text(json.dumps({'profile':{'critical':'kept'},'notes':[]}), encoding='utf8')
    with pytest.raises(ValueError, match='backup exists'):
        c.load_memory()


def test_memory_snake_case_search_profile_and_notes(memory):
    c.run_tool({'action':'save', 'category':'profile',
                'key':'favorite programming language', 'value':'Python'})
    assert 'favorite_programming_language' in c.run_tool({'action':'read', 'query':'programming',
                                                        'category':'profile'})['profile']
    c.run_tool({'action':'save','value':'a note','project':'my_project_config'})
    assert len(c.run_tool({'action':'read','query':'config','category':'note'})['notes']) == 1


def test_memory_empty_project_does_not_clear_on_text_edit(memory):
    nid = c.run_tool({'action':'save','value':'Keep this note', 'project':'tgbot'})['saved']
    assert c.run_tool({'action':'save','id':nid, 'value':'Updated note', 'project':''})['updated'] == nid
    row = c.run_tool({'action':'read','query':'Updated'})['notes'][0]
    assert row['text'] == 'Updated note' and row['project'] == 'tgbot'
    assert c.load_memory()['notes'][0]['project'] == 'tgbot'
    with pytest.raises(ValueError, match='nothing to update'):
        c.run_tool({'action':'save','id':nid,'project':''})


def test_memory_explicitly_clear_project_tag(memory):
    nid = c.run_tool({'action':'save','value':'Keep this note', 'project':'old_tag'})['saved']
    assert c.run_tool({'action':'save','id':nid, 'clear_project':True})['updated'] == nid
    assert 'project' not in c.run_tool({'action':'read','query':'Keep'})['notes'][0]
    assert c.load_memory()['notes'][0]['project'] is None
    assert c.run_tool({'action':'save','id':nid,'clear_project':True})['unchanged'] == nid


def test_memory_clear_project_rejects_ambiguous_or_invalid_actions(memory):
    nid = c.run_tool({'action':'save','value':'Keep this note', 'project':'old_tag'})['saved']
    with pytest.raises(ValueError, match='cannot set'):
        c.run_tool({'action':'save','id':nid,'project':'new_tag','clear_project':True})
    with pytest.raises(ValueError, match='requires an existing note'):
        c.run_tool({'action':'save','value':'new','clear_project':True})
    with pytest.raises(ValueError, match='true or false'):
        c.run_tool({'action':'save','id':nid,'clear_project':'true'})
    assert c.load_memory()['notes'][0]['project'] == 'old_tag'


def test_memory_project_schema_explains_explicit_clear(memory):
    schema = c.TOOL_SCHEMA['properties']
    assert schema['clear_project']['type'] == 'boolean'
    assert 'empty string' in schema['project']['description']


@pytest.mark.parametrize('sentence', ['password - manager Bitwarden','pass: true'])
def test_memory_harmless_secret_detector_phrases(memory, sentence):
    assert c.run_tool({'action':'save','value':sentence})['ok']


@pytest.mark.parametrize('secret', [
    'password: abcd1234',
    'sk-' + 'Ab0'*10,
    'password - manager hunter2secret99',
    'password - manager Bitwarden hunter2secret99',
    'pass: true hunter2secret99',
])
def test_memory_secret_rejection_preserved(memory, secret):
    with pytest.raises(ValueError, match='secret'):
        c.run_tool({'action':'save','value':secret})


def test_datetime_initialize_explicit_null():
    result = d.handle(legacy('initialize',params=None))['result']
    assert result['protocolVersion'] in d.SUPPORTED_PROTOCOLS


def test_datetime_timezone_description_lists_actions():
    result = d.handle(legacy('tools/list',params={}))['result']
    desc = result['tools'][0]['inputSchema']['properties']['timezone']['description']
    for action in ('convert','localize','timestamp','add','now'):
        assert action in desc


def test_datetime_unknown_action_lists_supported_actions():
    with pytest.raises(ValueError, match='convert, timestamp, or localize'):
        d.run_tool({'action':'not_real'})


@pytest.mark.parametrize('module,handle,tool_name',SERVERS)
def test_json_string_arguments_consistently_supported(module,handle,tool_name,memory):
    args = ({'expr':'2+2'} if tool_name == 'calculate' else
            {'action':'now'} if tool_name == 'datetime_calendar' else
            {'action':'read'})
    result=handle(legacy('tools/call',params={'name':tool_name,'arguments':json.dumps(args)}))['result']
    assert result['isError'] is False, result


@pytest.mark.parametrize('module,handle,tool_name',SERVERS)
def test_invalid_json_string_returns_tool_error(module,handle,tool_name,memory):
    result=handle(legacy('tools/call',params={'name':tool_name,'arguments':'{"invalid'}))['result']
    assert result['isError'] is True


def test_math_factorial_536_boundary():
    value = int(m.calculate('factorial(536)'))
    assert value.bit_length() <= m.MAX_INT_BITS
    with pytest.raises(ValueError,match='536'):
        m.calculate('factorial(537)')


def test_math_matrix_negative_literals_separate_ast_limit():
    expr = 'det(['+','.join('['+','.join(str(-int(i==j)) for j in range(12))+']' for i in range(12))+'])'
    assert m.calculate(expr) in ('1','-1')
    assert m.calculate(expr,mode='matrix') in ('1','-1')
    # Scalar mode still enforces stricter complexity, not simply accepting any AST.
    with pytest.raises(ValueError,match='complex'):
        m.calculate('['+','.join('0' for _ in range(190))+']',mode='numeric')

@pytest.mark.skipif(os.name == 'nt', reason='POSIX permissions only')
def test_memory_shared_directory_requires_opt_in(tmp_path, monkeypatch):
    folder = tmp_path / 'trusted_shared'
    folder.mkdir()
    folder.chmod(0o777)
    path = folder / 'memo.json'
    monkeypatch.setattr(c, 'MEMORY_FILE', path)
    monkeypatch.setattr(c, 'BACKUP_FILE', folder / 'memo.json.bak')
    monkeypatch.setattr(c, 'LOCK_FILE', folder / 'memo.json.lock')
    monkeypatch.delenv('AI_MEMORY_TRUST_SHARED_DIR', raising=False)
    try:
        with pytest.raises(OSError, match='group/other writable'):
            c.run_tool({'action': 'save', 'value': 'first'})
        monkeypatch.setenv('AI_MEMORY_TRUST_SHARED_DIR','1')
        assert c.run_tool({'action': 'save', 'value': 'first'})['ok']
    finally:
        folder.chmod(0o700)


def test_memory_migration_marker_blocks_legacy_resurrection_even_if_old_copy_exists(memory):
    # Simulates a failed rename after an otherwise completed import.
    c._mark_legacy_migration()
    c.LEGACY_FILE.write_text(json.dumps({'profile': {'old': 'legacy'}, 'notes': []}), encoding='utf8')
    with pytest.raises(ValueError, match='migration'):
        c.load_memory()


def test_memory_backup_write_failure_does_not_commit_new_changes(memory, monkeypatch):
    c.run_tool({'action':'save','value':'first'})
    saved = json.loads(memory.read_text(encoding='utf8'))
    def broken_copy(*args, **kwargs):
        raise OSError('simulated backup I/O failure')
    monkeypatch.setattr(c, '_private_copy', broken_copy)
    with pytest.raises(OSError, match='backup I/O failure'):
        c.run_tool({'action':'save','value':'second'})
    assert json.loads(memory.read_text(encoding='utf8')) == saved


def test_memory_backup_orphan_error_explains_safe_reset(memory):
    c.BACKUP_FILE.write_text('metadata', encoding='utf8')
    with pytest.raises(ValueError) as raised:
        c.load_memory()
    message = str(raised.value)
    for fragment in (memory.name, c.BACKUP_FILE.name, '.migration-complete',
                     '.recovery-warning.json', '.corrupt-*', c.LEGACY_FILE.name,
                     'first archive', 'clean reset'):
        assert fragment in message


def test_memory_failed_migration_write_leaves_legacy_retryable(memory, monkeypatch):
    c.LEGACY_FILE.write_text(json.dumps({'profile': {'old': 'legacy'}, 'notes': []}), encoding='utf8')
    original = c._write_atomic
    def no_space(*_args, **_kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr(c, '_write_atomic', no_space)
    with pytest.raises(OSError, match='disk full'):
        c._import_legacy()
    assert not c._migration_marker_path().exists()
    assert not c._migration_pending_path().exists()
    assert not memory.exists()
    assert c.LEGACY_FILE.exists()
    monkeypatch.setattr(c, '_write_atomic', original)
    imported = c._import_legacy()
    assert imported['profile']['old'] == 'legacy'
    assert memory.exists()
    assert c._migration_marker_path().exists()
    assert not c._migration_pending_path().exists()
    assert c.LEGACY_FILE.with_name(c.LEGACY_FILE.name + '.migrated').exists()


def test_memory_recovery_warning_reappears_in_new_process_only(memory, monkeypatch):
    c.run_tool({'action':'save', 'value':'A'})
    c.run_tool({'action':'save', 'value':'B'})
    memory.write_text('{not valid', encoding='utf8')
    assert 'warning' in c.run_tool({'action':'read'})
    assert 'warning' not in c.run_tool({'action':'read'})
    assert c._recovery_notice_path().exists()
    fresh = load('compact_memory_mcp')
    for name in ('MEMORY_FILE', 'BACKUP_FILE', 'LOCK_FILE', 'LEGACY_FILE'):
        monkeypatch.setattr(fresh, name, getattr(c, name))
    assert 'warning' in fresh.run_tool({'action':'read'})
    assert 'warning' not in fresh.run_tool({'action':'read'})
    assert c._recovery_notice_path().exists()


def test_memory_interrupted_migration_never_resurrects_legacy(memory):
    c.LEGACY_FILE.write_text(json.dumps({'profile':{'old':'stale'},'notes':[]}), encoding='utf8')
    c._write_migration_marker(c._migration_pending_path())
    with pytest.raises(ValueError, match='interrupted migration'):
        c.load_memory()
    assert not memory.exists()
    assert c.LEGACY_FILE.exists()


def test_memory_migration_marker_failure_preserves_pending_guard(memory, monkeypatch):
    c.LEGACY_FILE.write_text(json.dumps({'profile':{'old':'legacy'},'notes':[]}), encoding='utf8')
    def failed_marker():
        raise OSError('simulated marker creation failure')
    monkeypatch.setattr(c, '_mark_legacy_migration', failed_marker)
    with pytest.raises(OSError, match='marker creation'):
        c._import_legacy()
    assert memory.exists()
    assert c._migration_pending_path().exists()
    assert not c._migration_marker_path().exists()
    memory.unlink()  # emulate lost primary; never reimport stale legacy
    with pytest.raises(ValueError, match='interrupted migration'):
        c.load_memory()


def test_memory_uncertain_migration_commit_keeps_pending_guard(memory, monkeypatch):
    c.LEGACY_FILE.write_text(json.dumps({'profile': {'old':'source'}, 'notes':[]}), encoding='utf8')
    original = c._write_atomic
    def commit_then_fail(data):
        original(data)
        raise OSError('simulated post-commit I/O failure')
    monkeypatch.setattr(c, '_write_atomic', commit_then_fail)
    with pytest.raises(OSError, match='post-commit'):
        c._import_legacy()
    assert memory.exists() and c._migration_pending_path().exists()
    assert c.LEGACY_FILE.exists()  # never retired until migration is confirmed
    memory.unlink()
    with pytest.raises(ValueError, match='interrupted migration'):
        c.load_memory()
