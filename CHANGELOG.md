# Changelog

All notable changes to Greenlock are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [Unreleased]

### Added
- **Coverage-based confidence across supported languages** (WS-1 multi-language).
  `confidence=full` now requires changed lines to be exercised by the suite, per language:
  - **JavaScript** — built-in V8 coverage (`NODE_V8_COVERAGE`); a line counts only if the
    *tightest* enclosing V8 range has `count > 0`. *Validated end-to-end.*
  - **Go** — `go test -coverprofile`; coverprofile blocks with `count > 0`.
  - **Rust** — `cargo-llvm-cov --lcov` (cargo has no built-in line coverage); LCOV `DA:`.
  - **Python** — stdlib `sys.settrace` (already shipped in 0.1.0).
  Parsers (V8 / coverprofile / LCOV) are unit-tested. **Fail-open everywhere**: missing
  toolchain, no profile, or no data for a file never blocks a green patch — coverage only
  *degrades* on positive evidence that changed code wasn't executed. `custom` verifier
  (arbitrary commands) has no generic coverage.
  Note: Go/Rust integration is not yet validated against a live toolchain in CI (none
  installed); the tested parsers + fail-open wiring make this safe (worst case: no-op).

## [0.1.0] — 2026-06-19

First public alpha. A deterministic verify-gate for AI-generated code changes:
a patch is applied **only if the oracle is green** (closed-world ✔, the project's own
tests pass, no regression), otherwise refused. Headline: **WRONG-APPLY = 0**.

### Added
- **gate** — verify-only (`python -m greenlock.gate <repo> <diff>`): closed-world check
  + the project's own test suite + regression vs. baseline → `merge` / `reject`.
  Exit `0`/`1` for CI and pre-commit hooks. Also a generate-and-gate mode.
- **execution isolation** — `--isolated` (or `GREENLOCK_DOCKER=1`) runs the whole gate
  inside a locked Docker container (`--network none`, read-only rootfs, non-root,
  dropped caps, CPU/mem/PID limits, repo mounted read-only). **Fail-closed** if Docker
  is unavailable. Honored consistently by the CLI and the MCP server.
- **testgen** — characterization (golden-master) tests so untested code gets a safety net.
- **danger-check** — rejects patches introducing `eval`/`exec`/`os.system`/`subprocess`/
  test-environment detection before the oracle runs. `--trust` / `GREENLOCK_TRUST` makes
  it advisory for trusted authors / self-CI.
- **delivery rails** — MCP server (`greenlock-mcp`) and a GitHub Action (`action.yml`).
- **closed-world grounding** — deep for Python & JavaScript; conservative (zero false
  positives) for Go & Rust via tree-sitter; other languages oracle-only.

### Hardened
- **Honest coverage-based confidence** — `confidence=full` now requires the *changed
  lines* to actually be exercised by the suite (stdlib `sys.settrace`, no new deps).
  Untested changes degrade → reject instead of a false MERGE. *(Python/pytest only —
  see Known limitations.)*
- **Fail-closed oracle** — a baseline/verify failure now yields a clean REJECT instead
  of crashing the gate.
- **closed-world index** includes symbols introduced by the patch (no false reject on
  multi-file changes).
- **isolation keys split** — `GREENLOCK_DOCKER` = strong whole-gate isolation everywhere;
  the weaker per-command runner moved to `GREENLOCK_VERIFIER_DOCKER`.

### Known limitations
- **Coverage-based confidence is Python/pytest only.** The `node`/`go`/`rust`/`custom`
  verifiers still treat a green suite as full confidence, so an untested change in those
  languages can still merge. Multi-language coverage is the next milestone.
- **Coverage uses in-process tracing** — execution in subprocesses spawned by the test
  suite is invisible to it; such suites may under-report coverage (fail-open: never
  blocks a green patch on a measurement gap).
- **Not a sandbox unless `--isolated`.** The verifier executes the repo's tests; run
  untrusted patches with `--isolated` (needs a built `greenlock:latest` image) or in an
  ephemeral environment. See `SECURITY.md`.
- Alpha: limited real-world validation; not yet on PyPI (install from source).

[0.1.0]: https://github.com/abaykopenov/greenlock/releases/tag/v0.1.0
