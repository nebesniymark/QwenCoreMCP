#!/usr/bin/env python3
"""A small, safe JSON-RPC MCP server for date/time and calendar operations."""
import re
import sys
import json
import math
import calendar as _calendar
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

SERVER_NAME = "datetime-calendar"
SERVER_VERSION = "1.1.0"
PROTOCOL_VERSION = "2024-11-05"

_TZ_ALIASES = {
    "msk": "Europe/Moscow", "мск": "Europe/Moscow", "москва": "Europe/Moscow",
    "spb": "Europe/Moscow", "санкт-петербург": "Europe/Moscow", "питер": "Europe/Moscow",
    "nyc": "America/New_York", "la": "America/Los_Angeles",
    "utc": "UTC", "gmt": "UTC", "z": "UTC",
}
_OFFSET_RE = re.compile(r"(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", re.I)
_tz_index = None


def _tz_lookup():
    """(lowercase full name -> name, unique city name -> name); built once."""
    global _tz_index
    if _tz_index is None:
        full, cities = {}, {}
        for n in available_timezones():
            full[n.lower()] = n
            cities.setdefault(n.rsplit("/", 1)[-1].lower().replace("_", " "), set()).add(n)
        _tz_index = (full, {c: next(iter(v)) for c, v in cities.items() if len(v) == 1})
    return _tz_index


def get_zone(name):
    """Return (tzinfo, label). Accepts IANA names in any case, unambiguous city names
    (Moscow, new york), a few abbreviations (MSK) and fixed offsets (UTC+3, +03:30)."""
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("timezone must be a non-empty name such as Europe/Moscow, Moscow or UTC+3")
    raw = name.strip()
    m = _OFFSET_RE.fullmatch(raw)
    if m:
        hours, minutes = int(m.group(2)), int(m.group(3) or 0)
        if hours > 14 or minutes > 59:
            raise ValueError(f"Invalid UTC offset: {name}")
        delta = timedelta(hours=hours, minutes=minutes)
        sign = m.group(1)
        return timezone(-delta if sign == "-" else delta), f"UTC{sign}{hours:02d}:{minutes:02d}"
    key = re.sub(r"[\s_]+", " ", raw.lower())
    full, cities = _tz_lookup()
    canonical = _TZ_ALIASES.get(key) or full.get(key.replace(" ", "_")) or cities.get(key)
    for candidate in ([canonical] if canonical else []) + [raw]:   # raw = last resort, as-is
        try:
            return ZoneInfo(candidate), candidate
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    raise ValueError(f"Unknown timezone: {name}. Use an IANA name (Europe/Moscow), a city (Moscow) or an offset (UTC+3)")


def parse_iso_datetime(value, field):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError(f"{field} must be an ISO 8601 datetime string")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid {field}; use ISO 8601, e.g. 2026-11-03T14:30:00+00:00")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset or Z; naive datetimes are not accepted")
    return dt


def add_months(dt, months):
    if type(months) is not int or abs(months) > 120000:
        raise ValueError("months must be an integer between -120000 and 120000")
    month_index = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(month_index, 12)
    if not 1 <= year <= 9999:
        raise ValueError("Resulting year is outside Python's supported range")
    month = month0 + 1
    day = min(dt.day, _calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def run_tool(args):
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    action = args.get("action")

    if action == "now":
        tz, tz_label = get_zone(args.get("timezone") or "UTC")   # null / "" -> UTC
        current = datetime.now(tz)
        return {
            "timezone": tz_label,
            "iso": current.isoformat(timespec="seconds"),
            "date": current.date().isoformat(),
            "time": current.strftime("%H:%M:%S"),
            "unix_timestamp": int(current.timestamp()),
            "utc_offset": current.strftime("%z"),
            "weekday": current.strftime("%A"),
        }

    if action == "calendar":
        year, month = args.get("year"), args.get("month")
        if type(year) is not int or not 1 <= year <= 9999:
            raise ValueError("year must be an integer from 1 to 9999")
        if type(month) is not int or not 1 <= month <= 12:
            raise ValueError("month must be an integer from 1 to 12")
        weeks = _calendar.Calendar(firstweekday=_calendar.MONDAY).monthdayscalendar(year, month)
        return {
            "year": year,
            "month": month,
            "month_name": _calendar.month_name[month],
            "week_starts_on": "Monday",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "weeks": weeks,
        }

    if action == "weekday":
        value = args.get("date")
        if not isinstance(value, str) or len(value) > 20:
            raise ValueError("date must use YYYY-MM-DD format")
        try:
            d = date.fromisoformat(value)
        except ValueError:
            raise ValueError("Invalid date; use YYYY-MM-DD")
        return {"date": d.isoformat(), "weekday": d.strftime("%A"), "weekday_number_monday_1": d.isoweekday(), "is_weekend": d.isoweekday() >= 6}

    if action == "add":
        dt = parse_iso_datetime(args.get("datetime"), "datetime")
        unit = args.get("unit")
        amount = args.get("amount")
        if unit not in {"seconds", "minutes", "hours", "days", "weeks", "months", "years"}:
            raise ValueError("unit must be seconds, minutes, hours, days, weeks, months, or years")
        if type(amount) not in (int, float) or not math.isfinite(amount) or abs(amount) > 1000000:
            raise ValueError("amount must be a finite number with absolute value <= 1000000")
        if unit in {"months", "years"}:
            if type(amount) is not int:
                raise ValueError("amount must be an integer for months and years")
            result = add_months(dt, amount if unit == "months" else amount * 12)
        else:
            kwargs = {unit: amount}
            result = dt + timedelta(**kwargs)
        return {"input": dt.isoformat(), "result": result.isoformat(timespec="seconds"), "unit": unit, "amount": amount}

    if action == "difference":
        start = parse_iso_datetime(args.get("start"), "start")
        end = parse_iso_datetime(args.get("end"), "end")
        seconds = (end - start).total_seconds()
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "seconds": seconds,
            "minutes": seconds / 60,
            "hours": seconds / 3600,
            "days": seconds / 86400,
            "direction": "end_after_start" if seconds > 0 else "end_before_start" if seconds < 0 else "same_instant",
        }

    raise ValueError("Unknown action. Use now, calendar, weekday, add, or difference")


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
    sys.stdout.flush()


def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(req):
    if not isinstance(req, dict):
        return rpc_error(None, -32600, "Invalid Request")
    method = req.get("method")
    has_id = "id" in req
    mid = req.get("id")

    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if not has_id:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["now", "calendar", "weekday", "add", "difference"]},
                "timezone": {"type": "string", "description": "IANA name (Europe/Moscow), city (Moscow) or offset (UTC+3); default UTC; used by now"},
                "year": {"type": "integer", "minimum": 1, "maximum": 9999},
                "month": {"type": "integer", "minimum": 1, "maximum": 12},
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format; used by weekday"},
                "datetime": {"type": "string", "description": "ISO 8601 datetime with UTC offset; used by add"},
                "unit": {"type": "string", "enum": ["seconds", "minutes", "hours", "days", "weeks", "months", "years"]},
                "amount": {"type": "number"},
                "start": {"type": "string", "description": "ISO 8601 datetime with UTC offset; used by difference"},
                "end": {"type": "string", "description": "ISO 8601 datetime with UTC offset; used by difference"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "datetime_calendar",
            "description": "Date and time utility: current time in an IANA timezone, monthly calendar, weekday lookup, ISO datetime arithmetic, and difference between two instants. Datetimes for add/difference must include a UTC offset or Z.",
            "inputSchema": schema,
        }]}}
    if method == "tools/call":
        params = req.get("params", {})
        if not isinstance(params, dict) or params.get("name") != "datetime_calendar":
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool or invalid parameters"}}
        try:
            arguments = params.get("arguments")
            result = run_tool({} if arguments is None else arguments)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "Datetime error: " + str(exc)}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp125x
        except Exception:
            pass
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            send(rpc_error(None, -32700, "Parse error"))
            continue
        try:
            response = handle(req)
        except Exception as exc:
            sys.stderr.write(f"[{SERVER_NAME}] internal error: {exc}\n")
            rid = req.get("id") if isinstance(req, dict) else None
            response = rpc_error(rid, -32603, "Internal error") if isinstance(req, dict) and "id" in req else None
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()
