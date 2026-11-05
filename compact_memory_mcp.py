#!/usr/bin/env python3
"""
Compact Memory 1.3: tiny zero-dependency MCP server for long-term memory
of small local LLMs (4B, short context).

Design goals: few actions/params, hard output budget, null-safe arguments,
safe storage (atomic write, backups, quarantine of corrupt files, file lock).
"""
import sys
import os
import re
import json
import time
import shutil
from pathlib import Path
from datetime import datetime, timezone
from difflib import SequenceMatcher
from contextlib import contextmanager

# ============================================================
# CONFIG
# ============================================================
SERVER_NAME = "compact-memory"
SERVER_VERSION = "1.3.1"
DEFAULT_PROTOCOL = "2024-11-05"

MEMORY_FILE = Path(os.environ.get("AI_MEMORY_FILE", str(Path.home() / ".ai_memory.json"))).expanduser()
LEGACY_FILE = Path.home() / ".ai_memory_v1.2.json"   # one-time import of the old file
BACKUP_FILE = MEMORY_FILE.with_name(MEMORY_FILE.name + ".bak")
LOCK_FILE = MEMORY_FILE.with_name(MEMORY_FILE.name + ".lock")

MAX_NOTE = 600            # chars per note (keeps notes short and context cheap)
MAX_PROFILE_VALUE = 300
MAX_KEY = 50
MAX_PROJECT = 60
MAX_NOTES = 300
MAX_PROFILE = 100

NOTES_LIMIT = 8           # max notes per read
NOTES_BUDGET = 2500       # max chars of notes per read (~700-1000 tokens)
PROFILE_BUDGET = 1500     # max chars of profile per read
PREVIEW = 300             # chars of each note shown in read

# (?<![^\W\d_]) = "not directly preceded by a letter": catches DB_PASSWORD=..., but not "Compass - ...".
# token/токен need a digit in the value, so prose like "токен - короткий срок" is allowed.
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|(?<![^\W\d_])(?i:pass(?:word|wd)?|pwd|пароль|secret|api[_ \-]?key)\s*[:=\-—]\s*\S{4,}"
    r"|(?<![^\W\d_])(?i:token|токен)\s*[:=\-—]\s*(?=\S*\d)\S{6,}"
    r"|[A-Fa-f0-9]{32,})"
)
# Long base64-looking runs are only treated as secrets when they look random
# (mixed case + digit, few slashes), so ordinary file paths are not blocked.
B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")

ALIASES = {
    "update": "save", "write": "save", "add": "save", "create": "save", "edit": "save",
    "search": "read", "get": "read", "list": "read", "find": "read",
    "remove": "delete", "forget": "delete",
}

# ============================================================
# HELPERS
# ============================================================
def log_debug(msg: str):
    sys.stderr.write(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] [{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def arg_s(a: dict, name: str) -> str:
    """Null-safe string argument: None -> ''."""
    v = a.get(name)
    return "" if v is None else str(v).strip()

_ID_RE = re.compile(r"(?:#|n_|id\s*[:=]?\s*)?(\d+)(?:\.0+)?", re.I)

def arg_id(a: dict):
    """Strict note id: 5, 5.0, "5", "#5", "n_5" -> 5; None/empty -> None.
    Anything else raises instead of guessing (stripping non-digits used to turn 5.0 into 50
    and delete the wrong note)."""
    v = a.get("id")
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        raise ValueError(f"id must be a whole number such as 5 (got {v}); call read to see ids")
    s = str(v).strip()
    if s.lower() in ("", "none", "null"):
        return None
    m = _ID_RE.fullmatch(s)
    if m:
        return int(m.group(1))
    raise ValueError(f'id must be a whole number such as 5 (got "{cut(s, 20)}"); call read to see ids')

def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).casefold().replace("ё", "е")).strip()

def tokens(s: str) -> list:
    return re.findall(r"\w+", norm(s))

def stem(t: str) -> str:
    """Crude prefix stemming: good enough for Russian/English endings."""
    return t[:5] if len(t) > 5 else t

def score(stems: list, text: str) -> int:
    h = norm(text)
    return sum(1 for s in stems if re.search(r"(?<!\w)" + re.escape(s), h))

def similar(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 20 and short in long_:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.93

def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

def pick_category(a: dict, default=None):
    c = arg_s(a, "category").lower()
    c = {"notes": "note", "memory": "note"}.get(c, c)
    return c if c in ("profile", "note", "all") else default

# ============================================================
# STORAGE
# ============================================================
@contextmanager
def file_lock():
    """Cross-process lock around read-modify-write."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+b") as lf:
        if os.name == "nt":
            import msvcrt
            for _ in range(50):
                try:
                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise OSError("memory file is busy, try again")
            try:
                yield
            finally:
                try:
                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _clean(data) -> dict:
    """Validate/repair structure; also migrates old string ids (n_xxxx) to integers."""
    if not isinstance(data, dict):
        raise ValueError("root is not an object")
    prof_raw = data.get("profile")
    profile = {str(k): str(v) for k, v in prof_raw.items()} if isinstance(prof_raw, dict) else {}
    notes_raw = data.get("notes") if isinstance(data.get("notes"), list) else []
    next_id = data.get("next_id") if isinstance(data.get("next_id"), int) else 1

    notes, used = [], set()
    for n in notes_raw:
        if not isinstance(n, dict):
            continue
        text = str(n.get("text") or "").strip()
        if not text:
            continue
        i = n.get("id")
        i = i if isinstance(i, int) and not isinstance(i, bool) and i not in used else None
        if i is not None:
            used.add(i)
        created = str(n.get("created_at") or now_utc())
        notes.append({
            "id": i, "text": text[:MAX_NOTE * 2],
            "project": (str(n.get("project")).strip() or None) if n.get("project") else None,
            "created_at": created, "updated_at": str(n.get("updated_at") or created),
        })
    next_id = max(next_id, max(used, default=0) + 1)
    for n in notes:
        if n["id"] is None:
            n["id"] = next_id
            next_id += 1
    return {"version": "1.3", "profile": profile, "notes": notes, "next_id": next_id}

def load_memory() -> dict:
    """OSError propagates (never mistaken for corruption). Corrupt JSON is quarantined."""
    src = MEMORY_FILE if MEMORY_FILE.exists() else (LEGACY_FILE if LEGACY_FILE.exists() else None)
    if src is None:
        return _clean({})
    try:
        return _clean(_read_json(src))
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        log_debug(f"{src.name} is corrupt: {exc}")
        if src == MEMORY_FILE:
            bad = MEMORY_FILE.with_name(f"{MEMORY_FILE.name}.corrupt-{int(time.time())}")
            shutil.move(str(MEMORY_FILE), str(bad))
            log_debug(f"corrupt file moved to {bad.name}")
        if BACKUP_FILE.exists():
            try:
                data = _clean(_read_json(BACKUP_FILE))
                log_debug("recovered from backup")
                try:
                    # Persist it: the corrupt file was moved away, so without this the next
                    # call would find no file at all and see an empty memory.
                    _write_atomic(data)
                except OSError as exc3:
                    log_debug(f"could not persist recovered data: {exc3}")
                return data
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc2:
                log_debug(f"backup unusable: {exc2}")
        return _clean({})

def _snapshot():
    """One snapshot per day (keeps 7). Protects against a bad delete overwritten by the rolling .bak."""
    if not MEMORY_FILE.exists():
        return
    snap = MEMORY_FILE.with_name(f"{MEMORY_FILE.name}.{datetime.now().strftime('%Y%m%d')}.bak")
    try:
        if not snap.exists():
            shutil.copy2(MEMORY_FILE, snap)
        for old in sorted(MEMORY_FILE.parent.glob(MEMORY_FILE.name + ".????????.bak"))[:-7]:
            old.unlink()
    except OSError as exc:
        log_debug(f"snapshot failed: {exc}")

def _write_atomic(data: dict):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_FILE.with_name(MEMORY_FILE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):
        try:
            os.replace(tmp, MEMORY_FILE)
            return
        except PermissionError:   # Windows: target briefly held open by another process
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))

def save_memory(data: dict):
    if MEMORY_FILE.exists():
        _snapshot()
        try:
            shutil.copy2(MEMORY_FILE, BACKUP_FILE)   # copy, not move
        except OSError as exc:
            log_debug(f"backup failed: {exc}")
    _write_atomic(data)

# ============================================================
# TOOL ACTIONS  (each returns (result, changed))
# ============================================================
def _looks_random(s: str) -> bool:
    return (any(c.isdigit() for c in s) and any(c.isupper() for c in s)
            and any(c.islower() for c in s) and s.count("/") < 3)

def check_secret(text: str):
    if SECRET_RE.search(text) or any(_looks_random(m.group(0)) for m in B64_RE.finditer(text)):
        raise ValueError("looks like a secret/password/token; not saved")

def tool_read(a: dict, st: dict):
    cat = pick_category(a, "all")
    stems = [stem(t) for t in tokens(arg_s(a, "query")[:200]) if len(t) > 1]
    out, hidden = {}, 0

    if cat in ("all", "profile"):
        prof, used = {}, 0
        for k, v in st["profile"].items():
            if stems and not score(stems, f"{k} {v}"):
                continue
            used += len(k) + len(v) + 6
            if used > PROFILE_BUDGET:
                hidden += 1
                continue
            prof[k] = v
        out["profile"] = prof

    if cat in ("all", "note"):
        rows = []
        for n in st["notes"]:
            sc = score(stems, f"{n['text']} {n['project'] or ''}") if stems else 0
            if stems and not sc:
                continue
            rows.append((sc, n["updated_at"], n["id"], n))
        rows.sort(key=lambda r: r[:3], reverse=True)   # best match first, then newest
        notes, used = [], 0
        for *_, n in rows:
            item = {"id": n["id"], "text": cut(n["text"], PREVIEW)}
            if n["project"]:
                item["project"] = n["project"]
            size = len(json.dumps(item, ensure_ascii=False))
            if len(notes) >= NOTES_LIMIT or used + size > NOTES_BUDGET:
                break
            notes.append(item)
            used += size
        hidden += len(rows) - len(notes)
        out["notes"] = notes

    if stems and not out.get("profile") and not out.get("notes"):
        out["hint"] = "no matches; try one shorter keyword, or read without query"
    elif hidden:
        out["more"] = hidden
        out["hint"] = "more exist; narrow with query"
    return out, False

def tool_save(a: dict, st: dict):
    key, nid = arg_s(a, "key"), arg_id(a)
    cat = pick_category(a)
    if cat not in ("profile", "note"):
        cat = "profile" if key and nid is None else "note"

    if cat == "profile":
        k = re.sub(r"\W+", "_", norm(key)).strip("_")[:MAX_KEY]
        v = arg_s(a, "value")
        if not k or not v:
            raise ValueError('profile needs key and value. Example: {"action":"save","category":"profile","key":"os","value":"Windows 11"}')
        if len(v) > MAX_PROFILE_VALUE:
            raise ValueError(f"value too long (max {MAX_PROFILE_VALUE} chars); shorten it")
        check_secret(v)
        if k not in st["profile"] and len(st["profile"]) >= MAX_PROFILE:
            raise ValueError("profile is full; delete unused keys first")
        old = st["profile"].get(k)
        if old == v:
            return {"ok": True, "unchanged": k}, False
        st["profile"][k] = v
        res = {"ok": True, "saved": k}
        if old is not None:
            res["replaced"] = cut(old, 80)
        return res, True

    # note
    text = arg_s(a, "value")
    project = arg_s(a, "project")[:MAX_PROJECT] or None
    if len(text) > MAX_NOTE:
        raise ValueError(f"note too long (max {MAX_NOTE} chars); shorten to 1-2 sentences")
    if text:
        check_secret(text)

    if nid is not None:   # edit existing note
        for n in st["notes"]:
            if n["id"] == nid:
                if not text and project is None:
                    raise ValueError("nothing to update: give value and/or project")
                if text:
                    n["text"] = text
                if project is not None:
                    n["project"] = project
                n["updated_at"] = now_utc()
                return {"ok": True, "updated": nid}, True
        raise ValueError(f"note id {nid} not found; call read to see ids")

    if not text:
        raise ValueError('note needs value. Example: {"action":"save","category":"note","value":"Uses SQLite for the bot","project":"tgbot"}')
    for n in st["notes"]:                      # exact duplicate: harmless, report quietly
        if norm(n["text"]) == norm(text):
            return {"ok": True, "duplicate_of": n["id"]}, False
    for n in st["notes"]:                      # near-duplicate: never drop silently
        if similar(n["text"], text):
            return {"ok": False, "not_saved": True, "similar_to": n["id"],
                    "existing": cut(n["text"], 120),
                    "hint": f"if this updates it, call save with id={n['id']}; otherwise reword"}, False
    if len(st["notes"]) >= MAX_NOTES:
        raise ValueError(f"memory full ({MAX_NOTES} notes); delete old notes first")
    new_id = st["next_id"]
    st["next_id"] += 1
    ts = now_utc()
    st["notes"].append({"id": new_id, "text": text, "project": project, "created_at": ts, "updated_at": ts})
    return {"ok": True, "saved": new_id}, True

def tool_delete(a: dict, st: dict):
    key, nid = arg_s(a, "key"), arg_id(a)
    cat = pick_category(a)
    if cat not in ("profile", "note"):
        cat = "note" if nid is not None else "profile"

    if cat == "profile":
        k = re.sub(r"\W+", "_", norm(key)).strip("_")[:MAX_KEY]
        if k not in st["profile"]:
            have = ", ".join(list(st["profile"])[:15]) or "(empty)"
            raise ValueError(f"profile key '{k}' not found. Existing: {have}")
        del st["profile"][k]
        return {"ok": True, "deleted": k}, True

    if nid is None:
        raise ValueError('delete needs id (note) or key (profile). Example: {"action":"delete","id":3}')
    before = len(st["notes"])
    st["notes"] = [n for n in st["notes"] if n["id"] != nid]
    if len(st["notes"]) == before:
        raise ValueError(f"note id {nid} not found; call read to see ids")
    return {"ok": True, "deleted": nid}, True

def run_tool(args) -> dict:
    if not isinstance(args, dict):
        raise ValueError('arguments must be an object. Example: {"action":"read","query":"python"}')
    action = arg_s(args, "action").lower()
    action = ALIASES.get(action, action)
    if action not in ("read", "save", "delete"):
        raise ValueError('action must be read, save or delete. Example: {"action":"read"}')

    # Every action takes the lock: load_memory() may repair a corrupt file,
    # and that repair must not race with another process.
    with file_lock():
        st = load_memory()
        if action == "read":
            return tool_read(args, st)[0]
        res, changed = (tool_save if action == "save" else tool_delete)(args, st)
        if changed:
            save_memory(st)
        return res

# ============================================================
# MCP JSON-RPC
# ============================================================
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["read", "save", "delete"]},
        "category": {"type": "string", "enum": ["profile", "note"],
                     "description": "profile = permanent fact (key+value); note = project note (value)"},
        "key": {"type": "string", "description": "profile key, e.g. os"},
        "value": {"type": "string", "description": "profile value or note text"},
        "id": {"type": "integer", "description": "note id (from read) to edit or delete"},
        "project": {"type": "string", "description": "optional note tag"},
        "query": {"type": "string", "description": "read: 1-2 keywords; omit to list latest"},
    },
    "required": ["action"],
    "additionalProperties": False,
}

TOOL_DESCRIPTION = (
    "Long-term user memory. read: search by query or list latest. "
    "save: profile fact (key+value) or note (value); with id edits a note. "
    "delete: by id (note) or key (profile)."
)

def send_rpc(payload: dict):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()

def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

def handle_rpc(req):
    if not isinstance(req, dict):
        return None
    method, mid = req.get("method"), req.get("id")
    if (isinstance(method, str) and method.startswith("notifications/")) or mid is None:
        return None
    params = req.get("params") if isinstance(req.get("params"), dict) else {}

    if method == "initialize":
        pv = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv if isinstance(pv, str) else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "manage_memory", "description": TOOL_DESCRIPTION, "inputSchema": TOOL_SCHEMA}
        ]}}

    if method == "tools/call":
        if params.get("name") != "manage_memory":
            return rpc_error(mid, -32602, "Unknown tool name.")
        arguments = params.get("arguments", {})
        if isinstance(arguments, str):            # some clients send arguments as a JSON string
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = None
        try:
            text, is_err = json.dumps(run_tool(arguments), ensure_ascii=False, separators=(",", ":")), False
        except Exception as exc:
            text, is_err = f"Error: {exc}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": is_err}}

    return rpc_error(mid, -32601, f"Method '{method}' not found.")

def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp125x
        except Exception:
            pass
    log_debug(f"Starting Compact Memory MCP Server v{SERVER_VERSION}; file: {MEMORY_FILE}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req, resp = None, None
        try:
            req = json.loads(line)
            resp = handle_rpc(req)
        except json.JSONDecodeError:
            resp = rpc_error(None, -32700, "Parse error")
        except Exception as exc:
            log_debug(f"internal error: {exc}")
            rid = req.get("id") if isinstance(req, dict) else None
            resp = rpc_error(rid, -32603, "Internal error") if rid is not None else None
        if resp is not None:
            try:
                send_rpc(resp)
            except Exception as exc:
                log_debug(f"send failed: {exc}")

if __name__ == "__main__":
    main()
