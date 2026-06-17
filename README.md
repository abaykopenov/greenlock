# Greenlock

**A deterministic verify-gate for AI-generated code changes.**
A patch is applied **only if the oracle is green** — closed-world check passes, the
project's own test suite still passes, and no regression is introduced. Otherwise the
change is **refused**, untouched. Headline guarantee: **WRONG-APPLY = 0**.

Greenlock is **not another coding agent.** It is the safety layer that sits *between*
any agent (Devin, Copilot, Cursor, a local model — or a human) and your repository.
The model proposes; the oracle decides.

---

## Why

Today's coding agents *try* to run tests, but still hand you plausible-looking code that
may be broken — their guarantee is **probabilistic**. Review bots (Qodo, CodeRabbit)
*advise*; CI is dumb and *reactive*; security guardrails check policy, not correctness.

Greenlock gives a **refusal contract** instead:

| Layer | What it does | What it misses |
|---|---|---|
| CI / branch protection | deterministic, but dumb & reactive | needs tests up front; not in the agent's loop; no grounding |
| Review bots | comment / flag | advisory, probabilistic |
| Security guardrails | block secrets / policy / access | not correctness or regression |
| **Greenlock** | **deterministic merge/reject on correctness** | (only as strong as the test suite — see *testgen* on the roadmap) |

## How it works

```
patch (unified diff)  ─►  sandbox copy of repo
                          ├─ apply patch (git apply / patch)
                          ├─ closed-world check  (no references to symbols that don't exist)
                          ├─ run the project's OWN test suite, independently
                          └─ compare to baseline  → regression?
                                                   │
                          green + no regression ──►  ✅ MERGE
                          anything else         ──►  🛑 REJECT (with the failing reason)
```

Two modes:

- **gate-only** — verify a diff produced by *any* source. Model is never called. This is
  the product: an insurance layer over other agents.
- **generate-and-gate** — a local writer model proposes a patch and the same oracle
  loops repair → apply-only-if-green. Cheap, private, on-prem.

**Project-agnostic by design:** language is auto-detected per file (Python & JavaScript
get deep closed-world grounding; Go/Rust/Java/C/C++/C#/Ruby/PHP via tree-sitter), and the
verifier is auto-detected by manifest (`pyproject.toml`/`setup.py` → pytest,
`package.json` → node, `go.mod` → go test, `Cargo.toml` → cargo test). Drop in any repo.

## Quickstart

Requirements: **Python 3.10+**. For the *generate* mode and the web UI you also need an
[Ollama](https://ollama.com) endpoint; the *gate-only* mode needs neither.

```bash
git clone <your-fork-url> greenlock && cd greenlock
pip install -e .            # core (pure stdlib)
pip install -e ".[all]"     # + tree-sitter (more languages) + certifi (cloud HTTPS)
```

### Gate a diff (no model needed)

```bash
# verdict: MERGE or REJECT, with the reason
python3 -m greenlock.gate path/to/repo my_change.diff
git diff | python3 -m greenlock.gate path/to/repo -        # or from stdin
python3 -m greenlock.gate path/to/repo my_change.diff --json
```

Exit code is `0` on MERGE, `1` on REJECT — drop it straight into a CI step or pre-merge hook.

### Configuration

All settings resolve as **env var → `greenlock.local.json` → default**. The OSS default
endpoint is `http://localhost:11434`; keep your own endpoint/keys in a local file that is
git-ignored:

```jsonc
// greenlock.local.json  (git-ignored)
{ "ollama_url": "http://your-ollama:11434", "escalate_model": "gemini-2.5-flash" }
```

Env equivalents: `GREENLOCK_OLLAMA_URL`, `GREENLOCK_WRITER_MODEL`, `GREENLOCK_QA_MODEL`,
`GREENLOCK_EMBED_MODEL`, `GREENLOCK_ESCALATE`, `GREENLOCK_GEMINI_KEY`. Cloud API keys are
read from `GEMINI_API_KEY`/`GOOGLE_API_KEY` or a git-ignored `.gemini_key` file — never
committed.

### Web UI (grounded Q&A + code-edit mode)

```bash
python3 webapp/server.py 8012   # → http://127.0.0.1:8012
```

### Benchmarks

A non-circular benchmark (independent test suites as anchors + adversarial tasks proven
to break them) measures the headline metric on every run:

```bash
python3 -m benchmarks.run_bench pricing       # Python
python3 -m benchmarks.run_bench node_pricing  # JavaScript
```

## Status & roadmap

Alpha. The gate (closed-world + oracle + regression, gate-only & generate-and-gate) and
the web UI work today.

- [ ] **testgen** — generate *characterization* tests so untested code gets a safety net
      (closes the "guarantee is only as strong as the suite" gap).
- [ ] **delivery rails** — MCP tool `verify_patch` (so any agent calls the gate) + a
      GitHub Action that blocks PRs.
- [ ] deeper closed-world for tree-sitter languages; dashboard; on-prem packaging.

## License

[Apache-2.0](LICENSE).
