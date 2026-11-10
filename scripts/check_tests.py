"""测试质量静态检查：拦住"看起来在测、其实没在测"的测试。不运行测试，只读代码。

用法（项目根目录下）：
    python scripts/check_tests.py            # 检查本仓库 tests/ 下所有 test_*.py
    python scripts/check_tests.py ../other   # 检查其他项目（读取它的 tests/ 和 cases/）

所有测试都检查：
- 没有断言（assert 或 pytest.raises）
- 常量断言：assert True、assert 1 == 1、assert x == x
- 吞掉异常：裸 except，或 except 里只有 pass / ...
- 没有理由的 skip

用到 api fixture 的接口测试另外检查：
- 必须有 @pytest.mark.case
- cases/*.yaml 里该用例的测试点属于 side_effect / state / concurrency 维度时，测试必须查数据库（用 db fixture）
- 只断言了 status_code（警告）
"""

import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_DIMENSIONS = {"side_effect", "state", "concurrency"}


def case_dimensions(cases_dir: Path) -> dict[str, set[str]]:
    dims: dict[str, set[str]] = {}
    for path in cases_dir.glob("*.yaml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        point_dim = {p.get("id"): p.get("dimension") for p in doc.get("test_points") or []}
        for case in doc.get("cases") or []:
            dims[case.get("id")] = {point_dim.get(ref) for ref in case.get("test_points") or []}
    return dims


def _is_pytest_attr(node: ast.AST, *names: str) -> bool:
    # 匹配 pytest.raises / pytest.skip / pytest.mark.case 这类属性链
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return isinstance(node, ast.Name) and node.id == "pytest" and tuple(reversed(parts)) == names


def _case_ids(func: ast.FunctionDef) -> list[str]:
    ids = []
    for deco in func.decorator_list:
        if isinstance(deco, ast.Call) and _is_pytest_attr(deco.func, "mark", "case") and deco.args:
            arg = deco.args[0]
            if isinstance(arg, ast.Constant):
                ids.append(str(arg.value))
    return ids


def _constant_assert(test: ast.expr) -> bool:
    if isinstance(test, ast.Constant):
        return True
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        left, right = test.left, test.comparators[0]
        both_constant = isinstance(left, ast.Constant) and isinstance(right, ast.Constant)
        return both_constant or ast.dump(left) == ast.dump(right)
    return False


def _only_status_code(asserts: list[ast.Assert]) -> bool:
    def mentions_status(node):
        return any(isinstance(n, ast.Attribute) and n.attr == "status_code" for n in ast.walk(node))

    return bool(asserts) and all(mentions_status(a.test) for a in asserts)


def check_function(func: ast.FunctionDef, dims: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    nodes = list(ast.walk(func))
    asserts = [n for n in nodes if isinstance(n, ast.Assert)]
    raises = [
        n for n in nodes if isinstance(n, ast.Call) and _is_pytest_attr(n.func, "raises")
    ]
    if not asserts and not raises:
        errors.append("no assertion")
    for node in asserts:
        if _constant_assert(node.test):
            errors.append(f"line {node.lineno}: constant assertion")
    for node in nodes:
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                errors.append(f"line {node.lineno}: bare except")
            elif all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)) for s in node.body):
                errors.append(f"line {node.lineno}: exception swallowed")
        if isinstance(node, ast.Call) and _is_pytest_attr(node.func, "skip") and not (node.args or node.keywords):
            errors.append(f"line {node.lineno}: skip without reason")
    for deco in func.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if _is_pytest_attr(target, "mark", "skip") and not (isinstance(deco, ast.Call) and (deco.args or deco.keywords)):
            errors.append("@pytest.mark.skip without reason")
        if _is_pytest_attr(target, "mark", "skipif") and not any(k.arg == "reason" for k in getattr(deco, "keywords", [])):
            errors.append("@pytest.mark.skipif without reason")

    params = {a.arg for a in func.args.args}
    if "api" not in params:
        return errors, warnings
    case_ids = _case_ids(func)
    if not case_ids:
        errors.append("api test without @pytest.mark.case")
    needs_db = sorted(c for c in case_ids if dims.get(c, set()) & DB_DIMENSIONS)
    if needs_db and "db" not in params:
        errors.append(f"{', '.join(needs_db)} covers {sorted(DB_DIMENSIONS)} but the test does not query db")
    if _only_status_code(asserts):
        warnings.append("only status_code is asserted")
    return errors, warnings


def check_file(path: Path, dims: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    name = path.resolve().relative_to(ROOT).as_posix() if path.resolve().is_relative_to(ROOT) else path.as_posix()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            errs, warns = check_function(node, dims)
            errors += [f"{name}::{node.name}: {e}" for e in errs]
            warnings += [f"{name}::{node.name}: {w}" for w in warns]
    return errors, warnings


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    # 用法：check_tests.py [被测项目根目录]，根目录下有 tests/ 和 cases/（默认本仓库）
    root = Path(args[0]).resolve() if args else ROOT
    tests_dir = root / "tests"
    dims = case_dimensions(root / "cases")
    errors: list[str] = []
    warnings: list[str] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        errs, warns = check_file(path, dims)
        errors += errs
        warnings += warns
    for message in warnings:
        print(f"WARN  {message}")
    for message in errors:
        print(f"ERROR {message}")
    print(f"checked {tests_dir}: {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
