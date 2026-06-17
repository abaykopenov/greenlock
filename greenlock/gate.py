"""core.gate — verify-only: внешний unified-diff → детерминированный вердикт.

Сердце продукта №1 (слой верификации НАД агентами). Патч пришёл от ЛЮБОГО
источника — Devin/Copilot/Cursor/человек — мы только проверяем и решаем:
  merge   — closed-world ✔, оракул зелёный, регрессий нет;
  reject  — иначе (с конкретной причиной).

Модель здесь НЕ вызывается: это чистый детерминированный гейт. Тем и отличаемся
от review-ботов (они советуют, вероятностно) и от обычного CI (он тупой и не
делает closed-world): мы блокируем доказательно.

Если в репо нет тестов — оракул degraded → reject с честной причиной «гарантию
дать нельзя, нужен слой генерации тестов (testgen)». Это и есть стык с №4.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from greenlock import groundqa as g
from greenlock.config import OLLAMA_URL
from greenlock.adapters import detect_verifier
from greenlock.closed_world import closed_world_check
from greenlock.danger import scan_file
from greenlock.patch_applier import create_sandbox_dir, clean_sandbox_dir
from greenlock.code_writer import truncate_error_output

__all__ = ["verify_patch"]


def _changed_files(diff_text: str) -> list[str]:
    """Имена изменённых файлов из заголовков unified-diff (без префиксов a/ b/)."""
    seen: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("+++ ", "--- ")):
            p = line[4:].strip().split("\t")[0]
            if p == "/dev/null":
                continue
            if p.startswith(("a/", "b/")):
                p = p[2:]
            if p and p not in seen:
                seen.append(p)
    return seen


def _apply_diff(repo_dir: Path, diff_text: str) -> str | None:
    """Применить unified-diff в repo_dir. None = успех, иначе текст ошибки.

    Песочница — не git-репо (.git не копируется), но `git apply` это умеет;
    запасной путь — системный `patch`.
    """
    text = diff_text if diff_text.endswith("\n") else diff_text + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        diff_path = f.name
    last = ""
    try:
        for cmd in (
            ["git", "apply", "-p1", "--whitespace=nowarn", diff_path],
            ["git", "apply", "-p0", "--whitespace=nowarn", diff_path],
            ["patch", "-p1", "--no-backup-if-mismatch", "-s", "-i", diff_path],
        ):
            try:
                proc = subprocess.run(cmd, cwd=str(repo_dir),
                                      capture_output=True, text=True, timeout=30)
            except FileNotFoundError:
                continue  # нет git или patch — пробуем следующий
            if proc.returncode == 0:
                return None
            last = (proc.stderr or proc.stdout or "").strip()
        return last or "patch did not apply"
    finally:
        try:
            os.unlink(diff_path)
        except OSError:
            pass


def verify_patch(repo, diff_text: str, *, base_url: str | None = None,
                 extra_tests: dict | None = None) -> dict:
    """Проверить внешний unified-diff против репо. Вернуть вердикт-словарь.

    Ключи: decision (merge|reject), reasons[], changed_files[], closed_world[],
    failing_stage, regression, confidence, test_output.

    extra_tests — {rel-путь: содержимое} дополнительных тест-файлов (например
    характеризационных от greenlock.testgen). Кладутся в песочницу ДО снятия
    baseline, поэтому участвуют в оракуле наравне с родным сетом.
    """
    base_url = base_url or OLLAMA_URL
    repo_path = Path(repo).resolve()
    verdict = {
        "decision": "reject", "reasons": [], "changed_files": [],
        "closed_world": [], "danger": [], "failing_stage": None,
        "regression": False, "confidence": None, "test_output": "",
    }

    if not repo_path.is_dir():
        verdict["reasons"].append(f"нет репо: {repo}")
        return verdict
    changed = _changed_files(diff_text)
    if not changed:
        verdict["reasons"].append("в дифе не распознаны изменённые файлы")
        return verdict
    verdict["changed_files"] = changed

    index = g.build_index(repo_path)
    sandbox = create_sandbox_dir(repo_path)
    try:
        repo_copy = sandbox / repo_path.name
        # дополнительные тесты (характеризация) — ДО baseline, чтобы войти в оракул
        for rel, content in (extra_tests or {}).items():
            p = repo_copy / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        verifier = detect_verifier(sandbox)
        baseline = verifier.capture_baseline(sandbox)

        # baseline-содержимое изменённых файлов (до диффа) — для danger-диффа
        baseline_src = {}
        for rel in changed:
            ab = repo_copy / rel
            baseline_src[rel] = ab.read_text(encoding="utf-8") if ab.exists() else None

        err = _apply_diff(repo_copy, diff_text)
        if err:
            verdict["reasons"].append(f"диф не применился к репо: {err[:300]}")
            return verdict

        # closed-world: запрет ссылок на несуществующие символы
        cw: list[str] = []
        for rel in changed:
            ab = repo_copy / rel
            if ab.exists():
                cw += closed_world_check(ab, index.get("symbols", {}))
        if cw:
            verdict["closed_world"] = cw[:20]
            verdict["reasons"].append(
                f"closed-world: {len(cw)} ссыл(ок) на несуществующие символы — отказ")
            return verdict

        # danger-фильтр: опасные/обфусцирующие конструкции, ВНЕСЁННЫЕ патчем
        # (eval/exec, os.system/subprocess, детекция тест-окружения). ДО оракула —
        # чтобы вредоносный патч был отклонён, не успев исполниться.
        danger = []
        for rel in changed:
            for tag in scan_file(repo_copy / rel, baseline_src.get(rel)):
                danger.append(f"{rel}: {tag}")
        if danger:
            verdict["danger"] = danger[:20]
            verdict["reasons"].append(
                "danger: патч вносит опасные/обфусцирующие конструкции "
                f"({', '.join(danger[:6])}) — отказ (не исполнялось)")
            return verdict

        # оракул: синтаксис → тесты → регрессия vs baseline
        changed_for_verify = [str(Path(repo_path.name) / rel) for rel in changed]
        res = verifier.verify(sandbox, changed_for_verify, baseline=baseline)
        verdict["confidence"] = res.get("confidence")
        verdict["regression"] = bool(res.get("regression"))

        if res.get("passed") and res.get("confidence") == "full":
            verdict["decision"] = "merge"
            verdict["reasons"].append("оракул зелёный, регрессий нет — можно мержить")
            return verdict

        stage = next((s for s in res.get("stages", []) if not s.get("ok")), None)
        if stage:
            verdict["failing_stage"] = stage["name"]
            verdict["test_output"] = truncate_error_output(stage.get("output", ""))
            verdict["reasons"].append(
                f"оракул НЕ зелёный: стадия '{stage['name']}'"
                + (", РЕГРЕССИЯ родного сета" if verdict["regression"] else "")
                + " — отказ")
        elif res.get("confidence") != "full":
            verdict["failing_stage"] = "coverage"
            verdict["reasons"].append(
                "нет тестов, покрывающих изменение (confidence=degraded) — "
                "гарантию дать нельзя; нужен слой генерации тестов (testgen) — отказ")
        else:
            verdict["reasons"].append("оракул не дал полной уверенности — отказ")
        return verdict
    finally:
        clean_sandbox_dir(sandbox)


def main() -> int:
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser(
        description="verify-only gate: внешний unified-diff → вердикт merge|reject")
    ap.add_argument("repo", help="путь к репозиторию (любому)")
    ap.add_argument("diff", help="файл с unified-diff ('-' = stdin)")
    ap.add_argument("--json", action="store_true", help="вывести вердикт как JSON")
    a = ap.parse_args()

    diff_text = sys.stdin.read() if a.diff == "-" else \
        Path(a.diff).read_text(encoding="utf-8")
    v = verify_patch(a.repo, diff_text)

    if a.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0 if v["decision"] == "merge" else 1

    print(("✅ MERGE" if v["decision"] == "merge" else "🛑 REJECT") + f"  ({a.repo})")
    print("изменённые файлы:", ", ".join(v["changed_files"]) or "—")
    for r in v["reasons"]:
        print("  •", r)
    for e in v["closed_world"]:
        print("    cw:", e)
    for e in v.get("danger", []):
        print("    danger:", e)
    if v["test_output"]:
        print("  вывод оракула (хвост):")
        print("    " + v["test_output"].replace("\n", "\n    "))
    return 0 if v["decision"] == "merge" else 1


if __name__ == "__main__":
    raise SystemExit(main())
