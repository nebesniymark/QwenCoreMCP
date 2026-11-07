"""Shared fixtures and helpers for functional test groups."""

import importlib.util
import io
import json
import math
import os
import pathlib
import random
import stat
import subprocess
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from jsonschema import Draft202012Validator

import math_engine
import math_engine as calc
import math_engine as calculator
import math_engine as security_math
import memory_engine
import memory_engine as memory
import memory_engine as security_memory
import time_engine
import time_engine as calendar

release_BASE = pathlib.Path(__file__).resolve().parent.parent

def release_load(name):
    spec = importlib.util.spec_from_file_location(name, release_BASE / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

release_m = release_load('math_engine')

release_d = release_load('time_engine')

release_c = release_load('memory_engine')

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

audit_BASE=pathlib.Path(os.environ.get('MCP_AUDIT_SOURCE_DIR', str(pathlib.Path(__file__).resolve().parent.parent)))

audit_FILES=['math_engine','time_engine','memory_engine']

def audit_load(name):
    spec=importlib.util.spec_from_file_location('audit_'+name,audit_BASE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

audit_m=audit_load('math_engine'); audit_d=audit_load('time_engine'); audit_c=audit_load('memory_engine')

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




def qwen_rpc_tools(handler):
    result = handler({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "error" not in result
    return result["result"]["tools"]

regression_BASE = Path(__file__).resolve().parent.parent

def regression_load(name):
    spec = importlib.util.spec_from_file_location(name, regression_BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

regression_m = regression_load('math_engine')

regression_d = regression_load('time_engine')

regression_c = regression_load('memory_engine')

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



@pytest.fixture
def security_memory_file(tmp_path, monkeypatch):
    """Never read or write a real LM Studio memory profile during tests."""
    f = tmp_path / "isolated_memory.json"
    monkeypatch.setattr(security_memory, "MEMORY_FILE", f)
    monkeypatch.setattr(security_memory, "BACKUP_FILE", f.with_name(f.name + ".bak"))
    monkeypatch.setattr(security_memory, "LOCK_FILE", f.with_name(f.name + ".lock"))
    monkeypatch.setattr(security_memory, "LEGACY_FILE", tmp_path / "no_legacy.json")
    return f




























# Public helper surface used by functional test modules.
__all__ = [
    "Decimal",
    "Draft202012Validator",
    "ModuleType",
    "Path",
    "ThreadPoolExecutor",
    "ZoneInfo",
    "audit_BASE",
    "audit_FILES",
    "audit_c",
    "audit_d",
    "audit_load",
    "audit_m",
    "audit_memory",
    "audit_rpc_handler",
    "audit_rpc_request",
    "calc",
    "calculator",
    "calendar",
    "date",
    "datetime",
    "getcontext",
    "importlib",
    "io",
    "json",
    "math",
    "math_engine",
    "memory",
    "memory_engine",
    "np",
    "nullcontext",
    "os",
    "pathlib",
    "pytest",
    "qwen_rpc_tools",
    "random",
    "regression_BASE",
    "regression_SERVERS",
    "regression_c",
    "regression_d",
    "regression_legacy",
    "regression_load",
    "regression_m",
    "regression_memory",
    "regression_modern",
    "release_BASE",
    "release_MODULES",
    "release_c",
    "release_d",
    "release_load",
    "release_m",
    "release_memory",
    "release_rpc",
    "security_math",
    "security_memory",
    "security_memory_file",
    "stat",
    "subprocess",
    "sys",
    "time_engine",
    "timedelta",
    "timezone",
    "types",
]
