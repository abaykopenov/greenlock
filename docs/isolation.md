# Execution isolation (Docker)

Greenlock's verifier **executes code** (your test suite + generated tests). On its own
it is *not* a sandbox — see [../SECURITY.md](../SECURITY.md). For untrusted diffs on a
host you care about (local machine, persistent server, hosted service), run the whole
gate inside a one-shot container so even a successful RCE is contained.

This is the **real fix** for "RCE via tests"; the AST `danger`-check is only
defense-in-depth on top.

## Build the image

```bash
docker build -t greenlock:latest .
```

If the analyzed repo has test dependencies, extend the image:

```dockerfile
FROM greenlock:latest
RUN pip install -r requirements.txt   # your repo's test deps
```

## Run the gate in isolation

Python wrapper (recommended):

```python
from greenlock.isolate import verify_patch_isolated
verdict = verify_patch_isolated("/path/to/repo", open("change.diff").read())
# тот же словарь, что у verify_patch, + verdict["isolated"] == True
```

CLI:

```bash
python -m greenlock.isolate /path/to/repo change.diff
# или: greenlock-gate-isolated /path/to/repo change.diff
git diff | greenlock-gate-isolated /path/to/repo -
```

Raw `docker run` (what the wrapper does):

```bash
docker run --rm -i \
  --network none --read-only --tmpfs /tmp:rw,exec,size=1g \
  --memory 1g --cpus 2 --pids-limit 512 \
  --cap-drop ALL --security-opt no-new-privileges \
  -e GREENLOCK_SANDBOX_DIR=/tmp/gl-sandbox -e HOME=/tmp \
  -v /path/to/repo:/work/repo:ro \
  greenlock:latest \
  python -m greenlock.gate /work/repo - --json  < change.diff
```

## What the isolation gives you

- `--network none` — no exfiltration / no outbound calls.
- `--read-only` rootfs + repo mounted **read-only** — the host filesystem can't be
  touched; the only writable space is an ephemeral `tmpfs` at `/tmp`.
- non-root (`uid 65532`), `--cap-drop ALL`, `--security-opt no-new-privileges`.
- CPU / memory / PID limits; `--rm` destroys the container after the run.

Verified locally: a payload writing to the mounted repo fails with *Read-only file
system*, network calls fail name resolution, and no host file is created.

## Limits

Containers share the host kernel, so this is not a defense against kernel-level
container escapes. For high-assurance / hosted-SaaS use, run on a microVM runtime
(gVisor, Kata, Firecracker) — the `docker run` arguments above map onto those too.
