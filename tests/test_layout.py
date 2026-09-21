"""相依方向的自動檢查(/scaffold 慣例 8,2026-09-21 定案)。

規則三條,違反了就在這裡紅,不靠每次自己記得:

  1. `src/` 與 `tests/` 不准指到 `experiments/`(import 或路徑字串都算)
  2. `experiments/` 底下一個資料夾 = 一個實驗。實驗之間不准互相 import,
     同一包裡的檔案可以
  3. 實驗可以 import `src/`——EvalBench 的實驗量的就是評測台本身,
     複製一份 scoring 進實驗裡就不是在量那一支了

本檔自己要掃 experiments/,所以它是規則 1 的唯一例外(EXEMPT)。
docstring 不算違規:講到舊路徑的說明文字不會讓碼耦合。

跑法:
    docker compose run --rm app uv run pytest tests/test_layout.py -s
"""

import ast
from pathlib import Path

# §1 掃描工具
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = REPO_ROOT / "experiments"
EXEMPT = {Path(__file__).resolve()}


def py_files(*dirs: Path) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        out += [p for p in d.rglob("*.py") if p.resolve() not in EXEMPT]
    return sorted(out)


def non_docstring_strings(tree: ast.AST) -> list[str]:
    """所有字串常數,扣掉 module / class / def 的 docstring。"""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def imported_names(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module.split(".")[0])
    return names


# §2 三條規則
def test_系統與測試不指到實驗():
    """規則 1:刪掉整個 experiments/,src/ 與 tests/ 都不能受影響。"""
    experiment_modules = {p.stem for p in EXPERIMENTS.rglob("*.py")}
    bad = []
    for p in py_files(REPO_ROOT / "src", REPO_ROOT / "tests"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for name in imported_names(tree):
            if name in experiment_modules:
                bad.append(f"{p.relative_to(REPO_ROOT)}:import {name}")
        for s in non_docstring_strings(tree):
            if "experiments/" in s:
                bad.append(f"{p.relative_to(REPO_ROOT)}:字串 {s!r}")
    assert not bad, "src/ 或 tests/ 指到了 experiments/:\n  " + "\n  ".join(bad)


def test_實驗之間不互相import():
    """規則 2:一個資料夾一個實驗,包與包之間不准互相 import。"""
    # 模組名 → 它住哪一包(experiments/ 底下的第一層目錄之後的整條路徑)
    owner = {p.stem: p.parent for p in EXPERIMENTS.rglob("*.py")}
    bad = []
    for p in sorted(EXPERIMENTS.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for name in imported_names(tree):
            home = owner.get(name)
            if home is not None and home != p.parent:
                bad.append(
                    f"{p.relative_to(REPO_ROOT)} import {name}"
                    f"(住在 {home.relative_to(REPO_ROOT)})"
                )
    assert not bad, "實驗之間互相 import:\n  " + "\n  ".join(bad)


def test_每支實驗都住在自己的包裡():
    """規則 2 的另一半:`experiments/WXX/` 底下不准直接躺 .py,要在某一包裡面。"""
    loose = [
        p.relative_to(REPO_ROOT)
        for week in EXPERIMENTS.iterdir()
        if week.is_dir()
        for p in week.glob("*.py")
    ]
    assert not loose, "這幾支沒有分包,直接躺在週目錄底下:\n  " + "\n  ".join(map(str, loose))
