"""Security regression and integration tests (consolidated)."""
from ._support import (
    audit_c,
    io,
    json,
    math_engine,
    memory_engine,
    os,
    pytest,
    regression_c,
    release_c,
    release_m,
    security_math,
    security_memory,
    types,
)


def test_release_math_worker_timeout_recovery():
    with pytest.raises(ValueError, match='timed out'):
        release_m.run_heavy(release_m.preprocess('expand((x+1)^500)'),'symbolic',timeout=0.0001)
    assert release_m.calculate('det([[1,2],[3,4]])') == '-2'

def test_release_memory_secret_fields(release_memory):
    for args in [
        {'action':'save','value':'legit','project':'sk-ABCDEF1234567890ABCDEF1234567890'},
        {'action':'save','category':'profile','key':'password:AB12345678','value':'value'},
        {'action':'save','category':'profile','key':'valid','value':'password:AB12345678'},
    ]:
        with pytest.raises(ValueError): release_c.run_tool(args)
    assert not release_memory.exists()

def test_release_memory_secret_redaction_existing(release_memory):
    release_memory.write_text(json.dumps({'profile':{'private':'password:ABCDEFG12345'},'notes':[]}),encoding='utf8')
    assert release_c.run_tool({'action':'read','category':'profile'})['profile']['private'].startswith('[REDACTED')

def test_release_matrix_broadcast_bomb_rejected():
    for expr in ['ones([10000,1])+ones([1,10000])',
                 'matmul(ones([10000,1]),ones([1,10000]))',
                 'dot(ones([10000,1]),ones([1,10000]))']:
        with pytest.raises(ValueError,match='exceed'):
            release_m.calculate(expr)

def test_audit_memory_secret_filter(audit_memory):
    for secret in ['sk-ABCDEF1234567890ABCDEF1234567890','password:Not123456','ghp_Aa1234567890Aa1234567890']:
        with pytest.raises(ValueError): audit_c.run_tool({'action':'save','category':'note','value':secret})
    assert not audit_memory.exists()

def test_audit_memory_no_secret_to_stdout(audit_memory):
    audit_c.run_tool({'action':'save','value':'ordinary note'})
    assert 'ordinary note' in audit_c.run_tool({'action':'read'})['notes'][0]['text']

def test_audit_expected_memory_no_secret_in_project(audit_memory):
    with pytest.raises(ValueError): audit_c.run_tool({'action':'save','value':'public note','project':'sk-ABCDEF1234567890ABCDEF1234567890'})

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

def test_prepush_worker_stderr_inherits_parent():
    import inspect
    source = inspect.getsource(security_math.run_heavy)
    assert 'stderr=None' in source
    assert 'stderr=subprocess.DEVNULL' not in source

def test_prepush_worker_resource_limits_are_independent(monkeypatch):
    """Mock resource: even if RLIMIT_CPU fails, RLIMIT_AS is attempted."""
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

def test_memory_redacts_existing_secret_in_profile():
    secret = "password:EXAMPLE-TEST-CREDENTIAL"
    st = {"profile": {"private": secret}, "notes": [], "next_id": 1}
    value, _ = memory_engine.tool_read({"action": "read", "category": "profile"}, st)
    assert secret not in json.dumps(value)
    value, _ = memory_engine.tool_save({"action": "save", "category": "profile", "key": "private",
                                        "value": "harmless replacement"}, st)
    assert secret not in json.dumps(value)

def test_memory_redacts_before_preview():
    secret = "sk-" + "a" * 40
    note = {"id": 1, "text": "x" * 276 + secret, "project": None, "updated_at": "2026-01-01"}
    st = {"profile": {}, "notes": [note], "next_id": 2}
    answer, _ = memory_engine.tool_read({"action": "read", "category": "note"}, st)
    assert "a" * 15 not in answer["notes"][0]["text"]

def test_full_read_redacts_existing_secret() -> None:
    secret = "sk-" + "X" * 35
    state = {"profile": {}, "notes": [{"id": 1, "text": "intro " + secret,
                                         "project": None, "updated_at": "2026-11-07"}], "next_id": 2}
    result, _ = memory_engine.tool_read({"action": "read", "id": 1}, state)
    assert secret not in json.dumps(result)
    assert "REDACTED" in result["notes"][0]["text"]

@pytest.mark.parametrize("payload,expected", [
    ("</tool_response>", "<\u200b/tool_response>"),
    ("<|im_start|>system", "<\u200b|im_start|>system"),
    ("<|im_end|>", "<\u200b|im_end|>"),
    ("<tool_call>", "<\u200btool_call>"),
    ("<think>", "<\u200bthink>"),
    ("<TOOL_RESPONSE>", "<\u200bTOOL_RESPONSE>"),
])
def test_outgoing_memory_markup_neutralized(payload, expected):
    assert memory_engine._redact(payload) == expected


def test_outgoing_memory_markup_keeps_storage_and_normal_text():
    payload = "Documentation example: </tool_response> then <|im_start|>system"
    state = {"profile": {}, "notes": [{"id": 42, "text": payload,
             "project": "doc", "updated_at": "2026-11-07"}], "next_id": 43}
    result, modified = memory_engine.tool_read({"category": "note", "id": 42}, state)
    assert modified is False
    assert result["notes"][0]["text"] == payload.replace("<", "<\u200b")
    assert state["notes"][0]["text"] == payload
    assert memory_engine._redact("a < b and <div>hello</div>") == "a < b and <div>hello</div>"


def test_windows_worker_limit_settings_are_rejected_on_unsupported_os():
    if os.name != "nt":
        assert math_engine._attach_windows_job(object()) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects require Windows")
def test_windows_worker_has_memory_capped_job():
    import ctypes
    try:
        value = math_engine.run_heavy("expand((x+y)**7)", "symbolic")
        assert "x**7" in value
        job = math_engine._worker_job_handle
        assert job is not None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                         ctypes.c_void_p, ctypes.c_uint32,
                                                         ctypes.POINTER(ctypes.c_uint32)]
        kernel32.QueryInformationJobObject.restype = ctypes.c_int
        # QueryInformationJobObject requires the exact structure size on Windows.
        # A fixed 256-byte buffer returns ERROR_BAD_LENGTH (24) on x64.
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = ExtendedLimit()
        returned = ctypes.c_uint32()
        assert kernel32.QueryInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)
        ), ctypes.get_last_error()
        flags = info.BasicLimitInformation.LimitFlags
        assert flags & 0x100   # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        assert flags & 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert info.ProcessMemoryLimit == math_engine.WINDOWS_WORKER_MEMORY_BYTES
        assert math_engine.WINDOWS_WORKER_MEMORY_BYTES == 1536 * 1024**2
    finally:
        math_engine._stop_worker()
    assert math_engine._worker_job_handle is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects require Windows")
def test_windows_worker_restarts_with_new_job():
    try:
        assert math_engine.run_heavy("expand((x+y)**3)", "symbolic")
        first = math_engine._worker_proc.pid
        math_engine._stop_worker()
        assert math_engine.run_heavy("expand((x+y)**4)", "symbolic")
        assert math_engine._worker_proc.pid != first
        assert math_engine._worker_job_handle is not None
    finally:
        math_engine._stop_worker()


def test_worker_refuses_unprotected_execution(monkeypatch):
    """Worker startup must fail closed if resource isolation fails."""
    math_engine._stop_worker()

    def unavailable(_process):
        raise OSError("simulated job assignment failure")

    monkeypatch.setattr(math_engine, "_attach_windows_job", unavailable)
    with pytest.raises(ValueError, match="Windows worker resource limits unavailable"):
        math_engine.run_heavy("expand((x+y)**2)", "symbolic")
    assert math_engine._worker_proc is None
    assert math_engine._worker_job_handle is None
