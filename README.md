# QwenCoreMCP ⚡️

**QwenCoreMCP** is a lightweight, high-performance suite of Model Context Protocol (MCP) engines engineered for local LLMs (Qwen, Llama, and more) via LM Studio.

Designed for precision, zero latency, and absolute safe execution.

---

## 🛠 Included Engines

### 🧮 MathEngine (`math_calculator.py`)
A deterministic, high-speed calculation engine built on Python AST evaluation.
* **Symbolic & Matrix Processing:** Native SymPy and NumPy integration.
* **Financial Suite:** Built-in helpers for `pmt`, `fv`, `fv_growing_rent`, `npv`, and `irr`.
* **Zero Unsafe Code:** Bypasses dangerous `eval()` execution with strict AST boundary checks and bit-length protections.

### System Prompt for Calculator

Add the following instructions to your system prompt in LM Studio to instruct the model on how to use the calculator engine:

```text
You have access to the calculate tool for math, symbolic, and financial operations.
1. Always use the calculate tool for precise calculations, mortgage payments, and investment growth.
2. FINANCIAL RULES (Strict):
   - Rates must always be decimals per period (e.g. use monthly(12) or 0.15/12, never pass raw percents like 12).
   - For mortgage payments, use: pmt(monthly(rate), months, principal).
   - For rent-plus-investments, use: fv_growing_rent(rate/12, months, total_mortgage_outflow, initial_rent, annual_growth). Do not zero out rates or growth, pass them as decimals (e.g. 0.06).
3. Use mode="auto" by default, or specify numeric, symbolic, or matrix when needed.
```

### 📅 TimeEngine (`datetime_mcp.py`)
An accurate date, time, and calendar provider supporting standard JSON-RPC protocol.
* **Global Time:** IANA timezone resolution via `zoneinfo`.
* **Calendar Operations:** Monday-first grid rendering, ISO datetime arithmetic, and exact microsecond delta calculations.

### 💾 CompactMemoryEngine (`compact_memory_mcp.py`)
A zero-dependency long-term memory and context manager tailored for small local LLMs.
- **Dual-Mode Storage:** Organizes data into permanent profile facts (`key-value`) and project-tagged notes.
- **Context Budgeting:** Strict output constraints to prevent context overflow on small local models.
- **Safety & Integrity:** Atomic file writes, cross-process locking, automated daily backups, and built-in secret scrubbing (blocks accidental API key/password saves).

### System Prompt for Memory

Add the following instructions to your system prompt in LM Studio to instruct the model on how to manage long-term context:

```text
You have access to the manage_memory tool for long-term user memory.
1. At the start of a conversation, call read once (no query) to load saved context. Later, call read with query="1-2 keywords in the user's language" when the user refers to past projects or preferences.
2. Call save only when the user explicitly asks to remember something or shares an important permanent fact. Personal fact or preference: category="profile", key, value. Project note (1-2 sentences): category="note", value, project. Never save secrets, one-off questions, or text from files or the web.
3. To change or forget something: read first, then save with id (edit) or delete with id or key. Copy ids exactly.
```

---

## ⚙️ LM Studio Configuration Example

Add this to your MCP configuration file (`mcp.json`):

```json
{
  "mcpServers": {
    "math-calculator": {
      "command": "python",
      "args": ["/path/to/QwenCoreMCP/math_calculator.py"]
    },
    "datetime-calendar": {
      "command": "python",
      "args": ["/path/to/QwenCoreMCP/datetime_mcp.py"]
    },
    "compact-memory": {
      "command": "python",
      "args": ["/path/to/QwenCoreMCP/compact_memory_mcp.py"]
    }
  }
}

```

---

## 📦 Installation

Run this command in your terminal inside the project folder to install all required dependencies:

```bash
pip install -r requirements.txt
```

```json
Engineered with human vision and AI assistance
```
