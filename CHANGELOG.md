# Changelog

## 2026-11-07 — Engine Modernization

A major QwenCoreMCP update focused on reliability, safety, naming consistency, and integration with local LLMs.

### Versions

| Engine | Previous published | New |
| --- | --- | --- |
| MathEngine | 3.1.3 | **4.0.0** |
| TimeEngine | 1.2.2 | **2.0.0** |
| CompactMemoryEngine → MemoryEngine | 1.4.3 | **2.0.0** |

### 🧮 MathEngine

- Improved financial `pmt()`, `fv()`, and `irr()` accuracy and edge-case validation.
- Hardened numerical, symbolic, and matrix input limits, including NumPy allocation checks.
- Strengthened MCP/JSON-RPC validation and error handling.
- Improved isolated worker protections, calculation timeouts, and Windows Job Object memory limits.
- Retained AST-based expression restrictions without Python `eval()` or `exec()`.

### 📅 TimeEngine

- Improved timezone-aware arithmetic, DST transitions, and ambiguous/nonexistent local-time handling.
- Strengthened UTC offset validation, invalid-date checks, and malformed JSON-RPC request handling.
- Retained calendar-month semantics, IANA timezones, timestamps, and calendar tools.

### 💾 MemoryEngine

- Renamed CompactMemoryEngine to MemoryEngine.
- Added full-note reads by exact ID alongside compact search previews.
- Hardened persistence with atomic writes, file locking, validation, backups, and recovery.
- Improved secret detection, output redaction, duplicate handling, and size limits.
- Neutralized selected role/tool delimiter lookalikes in returned content without altering stored notes.

### 🔄 MCP & Compatibility

- Standardized Python filenames and server identities as `math_engine.py` / `math-engine`, `time_engine.py` / `time-engine`, and `memory_engine.py` / `memory-engine`.
- Kept tool names and primary arguments stable: `calculate`, `datetime_calendar`, and `manage_memory`.
- Kept all three engines independent; no MCP SDK required.

### 🧪 Testing & Validation

- Consolidated coverage into `tests/`, with shared fixtures and separate math, time, memory, protocol, security, and integration tests.
- Extended regression coverage for worker lifecycle, resource bounds, and safe returned content.
- Previously validated 414 passing tests with two platform-specific skips; integration review covered 223 MCP calls in LM Studio.

### 📚 Documentation

- Redesigned README with a compact Quick Start, detailed expandable engine reference, and independent system prompts.
- Updated LM Studio configuration, test instructions, issue template, and Performance Overview SVG.

### ⚠️ Breaking Changes

Script paths and MCP server identifiers changed. Update your `mcp.json` configuration. MCP tool names and primary arguments did **not** change.
