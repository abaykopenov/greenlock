# Security & threat model

**Read this before trusting Greenlock's verdict in an untrusted setting.**

Greenlock's guarantee — *apply only if green, else refuse* (`WRONG-APPLY = 0`) —
defends against **accidental breakage and hallucinations from a non-adversarial
author** (an AI model making mistakes, a typo, a regression). That is what it was
built for, and it does it well.

It is **not a sandbox**, and on its own it does **not** defend against a *malicious*
code author who is actively trying to fool the verifier.

## What the verifier actually does

To produce a verdict, Greenlock **executes code**: it copies the repo into
`.groundqa_sandbox/` and runs the project's own test suite (pytest, `node --test`, …)
**with the host interpreter, as the current user, with full filesystem and network
access.** The sandbox isolates *files* (for rollback), **not execution**.

> **Run Greenlock only in an isolated, ephemeral environment** — a GitHub-hosted
> runner, a fresh container, or a VM you can throw away. The provided GitHub Action
> runs on ephemeral runners for exactly this reason. Do **not** point it at untrusted
> diffs on a persistent host or your laptop without isolation.

## Known attacker capabilities (and mitigations)

1. **Arbitrary code execution via tests.** Any patch that adds or changes test code
   runs during verification. A malicious test can run `os.system(...)`, exfiltrate
   data, etc. — and the gate would still report MERGE if tests pass.
   - *Mitigation (defense-in-depth):* the **danger-check** ([greenlock/danger.py](greenlock/danger.py))
     rejects patches that *introduce* `os.system`/`subprocess`/`eval`/`exec`/… **before
     the oracle runs**, so the demonstrated payloads are refused without executing.
   - *Real fix:* run the whole gate inside a one-shot container — **opt-in Docker
     isolation is implemented**: `--network none`, read-only rootfs, repo mounted
     read-only, non-root, `--cap-drop ALL`, resource limits, `--rm`. See
     [docs/isolation.md](docs/isolation.md) (`python -m greenlock.isolate <repo> <diff>`).

2. **Closed-world bypass.** `closed_world` is a heuristic AST pre-filter, **not a
   security boundary**. Dynamic dispatch (`eval`, `getattr`, `__import__`) hides
   references from it.
   - *Mitigation:* the danger-check flags introduced `eval`/`exec`/`compile`/
     `__import__`/dynamic `getattr` and refuses such patches.

3. **Test-evasion ("two-faced" / defeat-device code).** Code that detects it is under
   test (e.g. `PYTEST_CURRENT_TEST`, `sys.modules` checks) and behaves correctly only
   then, while misbehaving in production. **This is a fundamental limit of any
   dynamic, test-based oracle** — it cannot be fully eliminated by running tests.
   - *Mitigation:* the danger-check flags introduced test-environment detection
     (`PYTEST_CURRENT_TEST`, `sys.modules` membership checks).
   - *Reducing risk further:* run tests in a production-like environment with
     test-only signals stripped where possible; add differential / mutation testing.

## What the danger-check is and isn't

It is **defense-in-depth**, not a sandbox. It only inspects Python (`.py`) and flags
constructs the **patch introduced** (existing project code is not penalized). A
determined adversary can obfuscate around an AST filter — so it raises the bar and
stops the obvious/demonstrated attacks, but **execution isolation remains the real
boundary**.

## Reporting a vulnerability

Open a GitHub issue (for non-sensitive findings) or contact the maintainer. Reproductions
and threat-model challenges are very welcome — this document exists because of one.

## Roadmap

- [x] Opt-in **Docker isolation** for the verifier (network-off, read-only, non-root,
      cap-drop, limits) — see [docs/isolation.md](docs/isolation.md).
- microVM runtime (gVisor/Kata/Firecracker) for hosted/high-assurance use.
- Configurable danger allow/deny lists.
- Danger-check for non-Python languages (via tree-sitter).
