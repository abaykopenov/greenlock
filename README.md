# Greenlock

**A deterministic verify-gate for AI-generated code changes.**
A patch is applied **only if the oracle is green** — closed-world check passes, the
project's own test suite still passes, and no regression is introduced. Otherwise the
change is **refused**, untouched. Headline guarantee: **WRONG-APPLY = 0**.

Greenlock is **not another coding agent.** It is the safety layer that sits *between*
any agent (Devin, Copilot, Cursor, a local model — or a human) and your repository.
The model proposes; the oracle decides.

> **Scope of the guarantee:** Greenlock defends against *accidental* breakage and
> hallucinations from a non-adversarial author. It is **not a sandbox** and the
> verifier **executes code** — run it only in an isolated/ephemeral environment, and
> read [SECURITY.md](SECURITY.md) for the threat model and a malicious-author's limits.

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

#### Run the gate in isolation (untrusted code)

The verifier **executes the repo's tests**, so for untrusted patches run the *whole gate*
inside a locked, throwaway Docker container — `--network none`, read-only rootfs, non-root,
all caps dropped, CPU/mem/PID limits, repo mounted read-only. Even an RCE in a test is
trapped in the container.

```bash
docker build -t greenlock:latest .                          # build the image once
git diff | python3 -m greenlock.gate path/to/repo - --isolated
```

`--isolated` is **fail-closed**: if Docker isn't available it rejects (never silently
downgrades to the unsafe path). Turn it on by default with `GREENLOCK_DOCKER=1`
(or `"docker": "1"` in `greenlock.json`); override the image via `--image` /
`GREENLOCK_DOCKER_IMAGE`; force it off for a single run with `--no-isolated`.
`GREENLOCK_DOCKER` drives the same strong isolation everywhere (CLI **and** the MCP
server). A separate, weaker per-command runner (test command in an official language
image) is opt-in via `GREENLOCK_VERIFIER_DOCKER` — distinct so the two never collide.

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

### Harden a repo that has no tests (`testgen`)

When the gate has nothing to verify against (`confidence=degraded`), Greenlock can
generate **characterization tests** (golden-master): the model proposes deterministic
scenarios, the *truth* is captured by **executing the current code** (not guessed), and
only stable, green-on-baseline tests are kept. `harden_and_verify` auto-generates them
for the changed files and re-verifies — so a behavior change is caught even in an
untested repo.

```python
from greenlock.testgen import generate_characterization_tests, harden_and_verify
generate_characterization_tests("path/to/repo", "module.py")   # → golden-master tests
harden_and_verify("path/to/repo", open("change.diff").read())  # gate + auto safety net
```

### Use it as a CI gate (GitHub Action)

Block any PR that breaks green. In the consuming repo's workflow:

```yaml
on: pull_request
jobs:
  greenlock:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }          # full history → PR diff
      - uses: actions/setup-python@v5
        with: { python-version: "3.x" }
      - run: pip install -e .              # install YOUR test deps (oracle runs your suite)
      - uses: abaykopenov/greenlock@main   # the gate: merge or block
```

The step exits non-zero on **reject**, so with branch protection the PR can't merge.

### Use it from an AI agent (MCP)

Expose the gate as a tool any MCP client (Claude Code, Cursor, …) can call:

```bash
pip install "greenlock[mcp]"
greenlock-mcp        # stdio MCP server; tools: verify_patch, harden_and_verify, generate_characterization_tests
```

### Benchmarks

A non-circular benchmark (independent test suites as anchors + adversarial tasks proven
to break them) measures the headline metric on every run:

```bash
python3 -m benchmarks.run_bench pricing       # Python
python3 -m benchmarks.run_bench node_pricing  # JavaScript
```

## Status & roadmap

Alpha. The gate (closed-world + oracle + regression; gate-only & generate-and-gate),
characterization `testgen`, the web UI, an MCP server, and a CI Action all work today.

- [x] **gate** — closed-world + oracle + regression, gate-only & generate-and-gate.
- [x] **testgen** — characterization tests so untested code gets a safety net.
- [x] **delivery rails** — MCP tool (`greenlock-mcp`) + GitHub Action (`action.yml`).
- [x] **nested-package & src-layout** support in `testgen`.
- [~] **closed-world for tree-sitter languages** — Go & Rust (conservative, zero
      false positives); other languages stay oracle-only until validated.
- [x] **danger-check** — rejects patches that introduce `eval`/`exec`/`os.system`/
      `subprocess`/test-environment detection, *before* the oracle runs (see [SECURITY.md](SECURITY.md)).
      `--trust` / `GREENLOCK_TRUST` makes it advisory (non-blocking) for trusted authors / self-CI.
- [x] **execution isolation** — opt-in `--isolated` (or `GREENLOCK_DOCKER=1`) runs the
      *whole gate* inside a locked Docker container (`--network none`, read-only rootfs,
      non-root, dropped caps, CPU/mem/PID limits, repo mounted read-only), **fail-closed**
      if Docker is unavailable. The real fix for untrusted-code RCE.
- [ ] more tree-sitter languages; PyPI release; dashboard; on-prem packaging.

## Security

Greenlock executes your test suite to produce a verdict, and its guarantee targets
*accidental* breakage — not a malicious author. See **[SECURITY.md](SECURITY.md)** for
the threat model, known attacker capabilities (RCE via tests, closed-world bypass,
test-evasion), and mitigations. Run the verifier only in an isolated/ephemeral
environment.

## License

[Apache-2.0](LICENSE).
