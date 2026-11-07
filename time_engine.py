#!/usr/bin/env python3
"""A small, safe JSON-RPC MCP server for date/time and calendar operations."""
import calendar as _calendar
import json
import logging
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

SERVER_NAME = "time-engine"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MODERN_PROTOCOL = "2026-07-28"
MAX_RPC_BYTES = 65536

_TZ_ALIASES = {
    "msk": "Europe/Moscow", "мск": "Europe/Moscow", "москва": "Europe/Moscow",
    "spb": "Europe/Moscow", "санкт-петербург": "Europe/Moscow", "питер": "Europe/Moscow",
    "nyc": "America/New_York", "la": "America/Los_Angeles",
    "utc": "UTC", "gmt": "UTC", "z": "UTC",
}
_OFFSET_RE = re.compile(r"(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", re.IGNORECASE)
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
    if raw.casefold() in ("utc", "z", "gmt"):
        return timezone.utc, "UTC"
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
    raise ValueError(f"Unknown timezone: {name}. IANA zones on Windows may require 'pip install tzdata'; UTC needs no database")


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

    if action == "convert":
        dt = parse_iso_datetime(args.get("datetime"), "datetime")
        tz, label = get_zone(args.get("timezone"))
        converted = dt.astimezone(tz)
        return {"input": dt.isoformat(), "timezone": label,
                "result": converted.isoformat(), "fold": converted.fold,
                "unix_timestamp": converted.timestamp()}

    if action == "timestamp":
        ts = args.get("timestamp")
        if type(ts) not in (int, float) or not math.isfinite(ts):
            raise ValueError("timestamp must be a finite number")
        unit = args.get("timestamp_unit") or "seconds"
        if unit not in ("seconds", "milliseconds"):
            raise ValueError("timestamp_unit: seconds or milliseconds")
        tz, label = get_zone(args.get("timezone") or "UTC")
        try:
            dt = datetime.fromtimestamp(ts / (1000 if unit == "milliseconds" else 1), timezone.utc).astimezone(tz)
        except (OverflowError, ValueError, OSError):
            raise ValueError("timestamp outside supported datetime range") from None
        return {"timezone": label, "result": dt.isoformat(), "fold": dt.fold}

    if action == "localize":
        value = args.get("datetime")
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError("datetime must be local ISO string without offset")
        try:
            local = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Invalid local ISO datetime") from None
        if local.tzinfo is not None:
            raise ValueError("localize requires datetime without UTC offset")
        tz, label = get_zone(args.get("timezone"))
        fold = args.get("fold", 0)
        if type(fold) is not int or fold not in (0, 1):
            raise ValueError("fold must be 0 or 1")
        candidate = local.replace(tzinfo=tz, fold=fold)
        # Round-trip through UTC to detect imaginary local times.
        roundtrip = candidate.astimezone(timezone.utc).astimezone(tz)
        if roundtrip.replace(tzinfo=None) != local:
            raise ValueError("Nonexistent local time during DST transition")
        return {"timezone": label, "result": candidate.isoformat(),
                "fold": candidate.fold, "unix_timestamp": candidate.timestamp()}

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
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
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
        if unit in {"months", "years"} and type(amount) is not int:
            raise ValueError("amount must be an integer for months and years")
        zone_arg = args.get("timezone")
        if zone_arg:
            tz, _ = get_zone(zone_arg)
            original = dt.astimezone(tz)
            if unit in {"seconds", "minutes", "hours"}:
                result = (dt.astimezone(timezone.utc) + timedelta(**{unit: amount})).astimezone(tz)
            else:
                naive = original.replace(tzinfo=None)
                if unit in {"months", "years"}:
                    naive = add_months(naive, amount if unit == "months" else amount * 12)
                else:
                    naive = naive + timedelta(**{unit: amount})
                fold = args.get("fold", original.fold if amount == 0 else 0)
                if type(fold) is not int or fold not in (0, 1):
                    raise ValueError("fold must be 0 or 1")
                result = naive.replace(tzinfo=tz, fold=fold)
                check = result.astimezone(timezone.utc).astimezone(tz)
                if check.replace(tzinfo=None) != naive:
                    raise ValueError("Resulting local time does not exist due to DST")
        elif unit in {"months", "years"}:
            result = add_months(dt, amount if unit == "months" else amount * 12)
        else:
            result = dt + timedelta(**{unit: amount})
        return {"input": dt.isoformat(), "result": result.isoformat(timespec="auto"), "unit": unit, "amount": amount}

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

    raise ValueError("Unknown action. Use now, calendar, weekday, add, difference, convert, timestamp, or localize")


def send(message):
    try:
        encoded = json.dumps(message, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        encoded = json.dumps(rpc_error(None, -32603, "Invalid response payload"))
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def rpc_validate(req):
    if not isinstance(req, dict):
        return rpc_error(None, -32600, "Invalid Request")
    rid = req.get("id")
    if (req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str)
            or not req["method"] or ("id" in req and
            (rid is None or isinstance(rid, bool) or type(rid) not in (str, int)))):
        return rpc_error(None, -32600, "Invalid Request")
    if "params" in req and req["params"] is not None and not isinstance(req["params"], dict):
        return rpc_error(rid, -32602, "Invalid params")
    return None


def _legacy_handle(req):
    if isinstance(req, dict) and "id" not in req:
        return None  # JSON-RPC notification
    invalid = rpc_validate(req)
    if invalid is not None:
        return invalid
    method = req.get("method")
    has_id = "id" in req
    mid = req.get("id")

    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if not has_id:
        return None
    if method == "initialize":
        params = req.get("params") or {}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion") if params.get("protocolVersion") in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["now", "calendar", "weekday", "add", "difference", "convert", "timestamp", "localize"]},
                "timezone": {"type": "string", "description": "IANA zone/city/UTC offset; used by now, add, convert, localize and timestamp; required for convert/localize"},
                "year": {"type": "integer", "minimum": 1, "maximum": 9999},
                "month": {"type": "integer", "minimum": 1, "maximum": 12},
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format; used by weekday"},
                "datetime": {"type": "string", "description": "ISO 8601: offset for add/convert, no offset for localize"},
                "unit": {"type": "string", "enum": ["seconds", "minutes", "hours", "days", "weeks", "months", "years"]},
                "amount": {"type": "number"},
                "start": {"type": "string", "description": "ISO 8601 datetime with UTC offset; used by difference"},
                "end": {"type": "string", "description": "ISO 8601 datetime with UTC offset; difference"},
                "timestamp": {"type": "number", "description": "Unix timestamp input"},
                "timestamp_unit": {"type": "string", "enum": ["seconds", "milliseconds"]},
                "fold": {"type": "integer", "enum": [0, 1], "description": "localize: DST overlap choice"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "datetime_calendar",
            "description": "Dates: now(timezone?), calendar(year,month), weekday(date), add(datetime,unit,amount,timezone?), difference(start,end), convert(datetime,timezone), timestamp(timestamp,timestamp_unit?), localize(datetime,timezone,fold?). add/difference/convert use offset ISO; localize uses naive ISO. Months clamp; add with timezone follows DST.",
            "inputSchema": schema,
        }]}}
    if method == "tools/call":
        params = req.get("params", {})
        if not isinstance(params, dict) or params.get("name") != "datetime_calendar":
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool or invalid parameters"}}
        try:
            arguments = params.get("arguments")
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            result = run_tool({} if arguments is None else arguments)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}}
        except Exception as exc:  # noqa: BLE001 - isolate failure at MCP/worker boundary
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "Datetime error: " + str(exc)}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}


def handle(request):
    """MCP 2026-07-28 modern mode + backward-compatible legacy handshake."""
    if isinstance(request, dict) and "id" not in request:
        return None  # JSON-RPC notifications never receive replies.
    invalid = rpc_validate(request)
    if invalid is not None:
        return invalid
    params = request.get("params") or {}
    meta = params.get("_meta") if isinstance(params, dict) else None
    version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    method = request["method"]
    if method == "server/discover" or version is not None:
        if version != MODERN_PROTOCOL:
            if method == "server/discover" and version is None:
                return rpc_error(request.get("id"), -32601, "Method not found")
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {
                "code": -32022, "message": "Unsupported protocol version",
                "data": {"supported": [MODERN_PROTOCOL, *SUPPORTED_PROTOCOLS], "requested": version if isinstance(version, str) else "[invalid version]"}}}
        if not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
            return rpc_error(request.get("id"), -32602, "Missing clientCapabilities")
        if "id" not in request:
            return None
        if method == "server/discover":
            result = {"resultType": "complete", "supportedVersions": [MODERN_PROTOCOL],
                      "capabilities": {"tools": {}}, "ttlMs": 0, "cacheScope": "private"}
        elif method in ("initialize", "ping"):
            return rpc_error(request.get("id"), -32601, "Method not supported in modern MCP")
        else:
            response = _legacy_handle(request)
            if response is None or "result" not in response:
                return response
            result = response["result"]
            result["resultType"] = "complete"
            if method == "tools/list":
                result["ttlMs"] = 0
                result["cacheScope"] = "private"
        result["_meta"] = {"io.modelcontextprotocol/serverInfo":
                           {"name": SERVER_NAME, "version": SERVER_VERSION}}
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
    return _legacy_handle(request)


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp125x
        except (OSError, ValueError, AttributeError) as exc:
            logging.getLogger(__name__).debug("Unable to reconfigure standard stream: %s", exc)
    while True:
        raw = sys.stdin.buffer.readline(MAX_RPC_BYTES + 1)
        if not raw:
            break
        if len(raw) > MAX_RPC_BYTES:
            if not raw.endswith(b"\n"):
                while raw and not raw.endswith(b"\n"):
                    raw = sys.stdin.buffer.readline(MAX_RPC_BYTES + 1)
            send(rpc_error(None, -32600, "Request too large"))
            continue
        if not raw.strip():
            continue
        try:
            req = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
            send(rpc_error(None, -32700, "Parse error"))
            continue
        try:
            response = handle(req)
        except Exception as exc:  # noqa: BLE001 - isolate failure at MCP/worker boundary
            sys.stderr.write(f"[{SERVER_NAME}] internal error: {exc}\n")
            rid = req.get("id") if isinstance(req, dict) else None
            response = rpc_error(rid, -32603, "Internal error") if isinstance(req, dict) and "id" in req else None
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()
