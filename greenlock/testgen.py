"""greenlock.testgen — генерация характеризационных тестов (golden-master).

Закрывает дыру «гарантия сильна настолько, насколько силён тест-сет»: для кода без
покрытия фиксирует ТЕКУЩЕЕ поведение, чтобы любое его изменение поймал гейт.

Подход execution-grounded: модель ПРЕДЛАГАЕТ детерминированные сценарии (setup + expr),
а ИСТИНУ (ожидаемый результат) берём из РЕАЛЬНОГО исполнения текущего кода в песочнице —
не из догадки модели. Оставляем только сценарии, что (1) исполняются без ошибок,
(2) детерминированы (два прогона — один repr), (3) зелены под pytest на baseline.

MVP: язык Python (.py). Для прочих — пометка в note.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from pathlib import Path

from greenlock.config import OLLAMA_URL, WRITER_MODEL
from greenlock.qa import generate
from greenlock.adapters import detect_adapters
from greenlock.patch_applier import create_sandbox_dir, clean_sandbox_dir

__all__ = ["generate_characterization_tests", "harden_and_verify"]

_SYS = ("Ты пишешь характеризационные тесты (golden-master): фиксируешь ТЕКУЩЕЕ "
        "поведение кода как есть, не оценивая его правильность.")

# Раннер исполнения сценариев: для каждого {setup, expr} даёт {ok, repr|error}.
_RUNNER = r'''import json, os, sys
sys.path.insert(0, os.getcwd())  # модуль репо лежит в cwd, а не в каталоге раннера
scenarios = json.load(open(sys.argv[1], encoding="utf-8"))
mod = sys.argv[2]
out = []
for sc in scenarios:
    ns = {}
    try:
        exec("from " + mod + " import *", ns)
        if sc.get("setup"):
            exec(sc["setup"], ns)
        val = eval(sc["expr"], ns)
        out.append({"ok": True, "repr": repr(val)})
    except Exception as e:
        out.append({"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
print(json.dumps(out))
'''


def _import_target(repo_path: Path, rel: str) -> tuple[str, str]:
    """rel-путь файла → (import_root, module).

    import_root — каталог (rel к repo), который надо положить в sys.path; module —
    dotted-имя модуля относительно него. Учитывает пакеты (__init__.py) и src-layout:
      pricing.py             → (".",   "pricing")
      pkg/sub/mod.py (пакеты)→ (".",   "pkg.sub.mod")
      src/shop/cart.py       → ("src", "shop.cart")   # src без __init__.py
    """
    file = repo_path / rel
    parts: list[str] = []
    d = file.parent
    while d != repo_path and (d / "__init__.py").exists():
        parts.insert(0, d.name)
        d = d.parent
    return str(d.relative_to(repo_path)), ".".join([*parts, file.stem])


def _public_symbols(target_file: str, content: str) -> list[str]:
    """Публичные символы файла как 'имя (kind)' (для подсказки модели)."""
    suffix = Path(target_file).suffix
    for a in detect_adapters():
        if suffix in a.extensions and a.name != "regex-fallback":
            try:
                res = a.parse(target_file, content)
            except Exception:
                return []
            return [f"{s['name']} ({s['kind']})" for s in res.symbols
                    if not s["name"].startswith("_")]
    return []


def _parse_scenarios(text: str) -> list[dict]:
    """Извлечь JSON-массив [{setup, expr}] из ответа модели."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    data = None
    try:
        data = json.loads(s)
    except Exception:
        m = re.search(r"\[.*\]", s, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if isinstance(data, dict) and isinstance(data.get("scenarios"), list):
        data = data["scenarios"]
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if isinstance(d, dict) and d.get("expr"):
            out.append({"setup": str(d.get("setup", "")), "expr": str(d["expr"])})
    return out


def _ask_scenarios(module, content, symbols, n, model, base_url, timeout):
    user = (
        f"Файл `{module}` (в тесте доступен как `from {module} import *`):\n"
        f"```python\n{content}\n```\n"
        f"Публичные символы: {', '.join(symbols) or '—'}\n\n"
        f"Придумай {n} РАЗНООБРАЗНЫХ ДЕТЕРМИНИРОВАННЫХ сценариев, покрывающих разные "
        f"ветки поведения этих символов. Запрещено: time, random, ввод-вывод, сеть, "
        f"глобальное изменяемое состояние. Используй ТОЛЬКО существующие символы файла.\n"
        f"Верни СТРОГО JSON-массив объектов вида "
        f'{{"setup": "<python: создать объекты/состояние>", "expr": "<выражение, чьё '
        f'значение фиксируем>"}}.\n'
        f"setup многострочный (через \\n) или пустой; expr — ОДНО выражение.\n"
        f'Пример: [{{"setup": "c = Cart(\\"gold\\")\\nc.add_item(\\"x\\", \\"10.00\\", 2)", '
        f'"expr": "c.total()"}}]\n'
        f"Никакого текста вне JSON."
    )
    args = types.SimpleNamespace(base_url=base_url, timeout=timeout)
    text, usage = generate(args, model, _SYS, user)
    return _parse_scenarios(text), usage


def _capture(repo_dir: Path, module: str, scenarios: list) -> list[dict]:
    """Исполнить сценарии в repo_dir (cwd) и вернуть [{ok, repr|error}]."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(scenarios, f)
        sc_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as f:
        f.write(_RUNNER)
        run_path = f.name
    try:
        proc = subprocess.run([sys.executable, run_path, sc_path, module],
                              cwd=str(repo_dir), capture_output=True, text=True,
                              timeout=30)
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            err = (proc.stderr or proc.stdout or "runner failed")[:200]
            return [{"ok": False, "error": "runner: " + err} for _ in scenarios]
    except Exception as e:
        return [{"ok": False, "error": f"{type(e).__name__}: {e}"} for _ in scenarios]
    finally:
        for p in (sc_path, run_path):
            try:
                os.unlink(p)
            except OSError:
                pass


_STAR_IMPORT = re.compile(r"^\s*from\s+\S+\s+import\s+\*")


def _emit_tests(module: str, kept: list[dict]) -> str:
    """Собрать pytest-файл: каждый сценарий → assert repr(expr) == зафиксированный repr.

    Из setup выбрасываются строки `from ... import *`: они легальны в module-level
    exec (где снимался capture), но запрещены ВНУТРИ функции — нужные имена уже даёт
    модульный `from {module} import *` в шапке файла.
    """
    lines = [
        '"""Характеризационные тесты (golden-master) — сгенерированы greenlock.testgen.',
        f"Фиксируют ТЕКУЩЕЕ поведение модуля {module}: любое изменение поведения "
        f"поймает гейт.",
        'НЕ редактировать вручную — перегенерировать через greenlock.testgen."""',
        f"from {module} import *  # noqa: F401,F403",
        "",
        "",
    ]
    for i, k in enumerate(kept):
        lines.append(f"def test_char_{i}():")
        for sl in (k["setup"] or "").split("\n"):
            if sl.strip() and not _STAR_IMPORT.match(sl):
                lines.append(f"    {sl}")
        lines.append(f"    assert repr({k['expr']}) == {json.dumps(k['repr'])}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _passed_from_junit(xml_path: Path):
    """Множество имён прошедших тест-кейсов из junit-xml (или None при ошибке)."""
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return None
    passed = set()
    for tc in root.iter("testcase"):
        bad = any(c.tag in ("failure", "error") for c in tc)
        if not bad:
            passed.add(tc.get("name", ""))
    return passed


def _green_filter(repo_path: Path, test_rel: str, module: str,
                  kept: list[dict]) -> list[dict]:
    """Прогнать сгенерированные тесты под pytest в песочнице, оставить только зелёные."""
    content = _emit_tests(module, kept)
    sandbox = create_sandbox_dir(repo_path)
    try:
        repo_copy = sandbox / repo_path.name
        target = repo_copy / test_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        xml = repo_copy / "_char_report.xml"
        subprocess.run([sys.executable, "-m", "pytest", test_rel,
                        f"--junitxml={xml}", "-q"],
                       cwd=str(repo_copy), capture_output=True, text=True, timeout=60)
        passed = _passed_from_junit(xml)
    except Exception:
        passed = None
    finally:
        clean_sandbox_dir(sandbox)
    if passed is None:
        return kept  # не смогли проверить — доверяем фильтру детерминизма
    return [k for i, k in enumerate(kept) if f"test_char_{i}" in passed]


def generate_characterization_tests(repo, target_file, *, symbols=None,
                                    base_url=None, model=None,
                                    max_scenarios=14, timeout=120) -> dict:
    """Сгенерировать характеризационные тесты для target_file (rel-путь в repo).

    Возвращает: test_file (rel), content (pytest-исходник), module, kept, dropped,
    covered_symbols, scenarios, note.
    """
    base_url = base_url or OLLAMA_URL
    model = model or WRITER_MODEL
    repo_path = Path(repo).resolve()
    rel = target_file
    out = {"test_file": None, "content": "", "module": None, "kept": 0,
           "dropped": 0, "covered_symbols": [], "scenarios": [], "note": ""}

    abs_target = repo_path / rel
    if abs_target.suffix != ".py":
        out["note"] = "MVP testgen поддерживает только .py"
        return out
    if not abs_target.exists():
        out["note"] = f"нет файла: {rel}"
        return out

    content = abs_target.read_text(encoding="utf-8")
    import_root_rel, module = _import_target(repo_path, rel)
    syms = symbols or _public_symbols(rel, content)

    scenarios, _ = _ask_scenarios(module, content, syms, max_scenarios,
                                  model, base_url, timeout)
    if not scenarios:
        out["note"] = "модель не дала валидных сценариев"
        return out

    # исполняем в песочнице (изоляция), дважды — для проверки детерминизма
    sandbox = create_sandbox_dir(repo_path)
    try:
        cap_cwd = sandbox / repo_path.name / import_root_rel
        run1 = _capture(cap_cwd, module, scenarios)
        run2 = _capture(cap_cwd, module, scenarios)
    finally:
        clean_sandbox_dir(sandbox)

    kept = [{**sc, "repr": r1["repr"]}
            for sc, r1, r2 in zip(scenarios, run1, run2)
            if r1.get("ok") and r2.get("ok") and r1["repr"] == r2["repr"]]
    if not kept:
        out["dropped"] = len(scenarios)
        out["note"] = "ни один сценарий не оказался стабильным/исполнимым"
        return out

    test_rel = str(Path(import_root_rel) / f"test_char_{Path(rel).stem}.py")
    kept = _green_filter(repo_path, test_rel, module, kept)
    if not kept:
        out["dropped"] = len(scenarios)
        out["note"] = "сгенерированные тесты не прошли pytest на baseline"
        return out

    sym_names = [s.split(" ")[0] for s in syms]
    covered = sorted({n for n in sym_names
                      if any(n in (k["setup"] + k["expr"]) for k in kept)})

    out.update(test_file=test_rel, content=_emit_tests(module, kept), module=module,
               kept=len(kept), dropped=len(scenarios) - len(kept),
               covered_symbols=covered, scenarios=kept)
    return out


def harden_and_verify(repo, diff_text, *, base_url=None, model=None,
                      timeout=120) -> dict:
    """Гейт с автогенерацией сетки: если verify_patch отказал из-за отсутствия
    покрытия (confidence=degraded), сгенерировать характеризацию для изменённых .py
    и перепроверить — теперь изменение поведения ловится."""
    from greenlock.gate import verify_patch
    base_url = base_url or OLLAMA_URL
    v = verify_patch(repo, diff_text, base_url=base_url)
    if v["decision"] == "merge" or v.get("failing_stage") != "coverage":
        return v

    extra, gen_meta = {}, []
    for rel in v["changed_files"]:
        if rel.endswith(".py"):
            res = generate_characterization_tests(repo, rel, base_url=base_url,
                                                  model=model, timeout=timeout)
            if res["kept"]:
                extra[res["test_file"]] = res["content"]
                gen_meta.append({"file": rel, "test_file": res["test_file"],
                                 "kept": res["kept"], "covered": res["covered_symbols"]})
    if not extra:
        v["testgen"] = {"note": "не удалось сгенерировать характеризацию"}
        return v

    v2 = verify_patch(repo, diff_text, base_url=base_url, extra_tests=extra)
    v2["testgen"] = {"generated": gen_meta}
    return v2
