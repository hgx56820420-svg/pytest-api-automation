"""校验测试设计文件 cases/<service>.yaml：需求规则 → 覆盖矩阵 → 测试点 → 用例 → pytest 是否连成一条链。

用法（项目根目录下）：
    python scripts/check_cases.py cases/mini_shop.yaml

有错误时退出码 1。自动化缺口（接口用例还没有对应 pytest）只打印警告，加 --strict 时也算错误。
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIMENSIONS = ("happy", "boundary", "invalid_input", "permission", "state", "side_effect", "concurrency")
TESTABLE = ("api", "ui", "manual")
_CASE_MARK = re.compile(r"""@pytest\.mark\.case\(\s*["']([^"']+)["']""")
_SPACES = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACES.sub("", text)


def _ids(items, kind, errors):
    seen = set()
    for item in items:
        item_id = item.get("id")
        if not item_id:
            errors.append(f"{kind}: entry without id: {item}")
        elif item_id in seen:
            errors.append(f"{kind}: duplicate id {item_id}")
        seen.add(item_id)
    return seen


def _operations(contract_path: Path) -> set[str]:
    spec = json.loads(contract_path.read_text(encoding="utf-8"))
    return {f"{method.upper()} {path}" for path, ops in spec.get("paths", {}).items() for method in ops}


def automated_case_ids(tests_dir: Path) -> set[str]:
    ids = set()
    for path in tests_dir.rglob("test_*.py"):
        ids.update(_CASE_MARK.findall(path.read_text(encoding="utf-8")))
    return ids


def check(doc: dict, *, root: Path = ROOT) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    service = doc.get("service")
    rules = doc.get("rules") or []
    coverage = doc.get("coverage") or {}
    points = doc.get("test_points") or []
    cases = doc.get("cases") or []

    # ① 规则：原文引用必须真的出现在需求文档里，接口必须存在于契约基线
    source = root / str(doc.get("source", ""))
    source_text = _norm(source.read_text(encoding="utf-8")) if source.is_file() else None
    if source_text is None:
        errors.append(f"source not found: {doc.get('source')}")
    contract = root / "contracts" / f"{service}.openapi.json"
    operations = _operations(contract) if contract.is_file() else None
    if operations is None:
        warnings.append(f"no contract baseline {contract.relative_to(root)}, api fields not checked")

    rule_ids = _ids(rules, "rules", errors)
    for rule in rules:
        quote = str(rule.get("quote") or "")
        if not quote.strip():
            errors.append(f"{rule.get('id')}: missing quote from the requirement document")
        elif source_text is not None and _norm(quote) not in source_text:
            errors.append(f"{rule.get('id')}: quote not found in {doc.get('source')}: {quote[:60]}")
        api = rule.get("api")
        if api and operations is not None and api not in operations:
            errors.append(f"{rule.get('id')}: api '{api}' not in contract baseline")

    for question in doc.get("questions") or []:
        if question.get("status") != "answered":
            errors.append(f"{question.get('id')}: open question must be answered first: {question.get('text')}")

    # ② 覆盖矩阵：每条规则的每个维度要么 test，要么 "n/a: 理由"
    wanted: set[tuple[str, str]] = set()
    for rule_id in rule_ids:
        row = coverage.get(rule_id)
        if not isinstance(row, dict):
            errors.append(f"coverage: missing row for {rule_id}")
            continue
        for dim in DIMENSIONS:
            value = str(row.get(dim, "")).strip()
            if value == "test":
                wanted.add((rule_id, dim))
            elif not (value.startswith("n/a:") and value[4:].strip()):
                errors.append(f"coverage {rule_id}.{dim}: must be 'test' or 'n/a: <reason>', got {value!r}")
        if not any((rule_id, dim) in wanted for dim in DIMENSIONS):
            errors.append(f"coverage {rule_id}: no dimension marked 'test'")
    for rule_id in coverage:
        if rule_id not in rule_ids:
            errors.append(f"coverage: unknown rule {rule_id}")

    # ③ 测试点：覆盖矩阵里每个 test 格子至少一个
    point_ids = _ids(points, "test_points", errors)
    covered = set()
    for point in points:
        key = (point.get("rule"), point.get("dimension"))
        if key not in wanted:
            errors.append(f"{point.get('id')}: {key[0]}.{key[1]} is not marked 'test' in coverage")
        covered.add(key)
    for rule_id, dim in sorted(wanted - covered):
        errors.append(f"coverage {rule_id}.{dim} is 'test' but has no test point")

    # ④ 用例：每个测试点至少一个；不能用接口测的要写原因
    case_ids = _ids(cases, "cases", errors)
    used_points = set()
    for case in cases:
        refs = case.get("test_points") or []
        if not refs:
            errors.append(f"{case.get('id')}: no test_points")
        for ref in refs:
            if ref not in point_ids:
                errors.append(f"{case.get('id')}: unknown test point {ref}")
            used_points.add(ref)
        if not case.get("expected"):
            errors.append(f"{case.get('id')}: missing expected result")
        testable = case.get("testable")
        if testable not in TESTABLE:
            errors.append(f"{case.get('id')}: testable must be one of {TESTABLE}")
        elif testable != "api" and not str(case.get("reason") or "").strip():
            errors.append(f"{case.get('id')}: testable={testable} needs a reason")
    for point_id in sorted(point_ids - used_points):
        errors.append(f"{point_id}: no case")

    # ⑤ 自动化：接口用例是否已有 @pytest.mark.case 对应的测试
    automated = automated_case_ids(root / "tests")
    api_cases = {c["id"] for c in cases if c.get("id") and c.get("testable") == "api"}
    missing = sorted(api_cases - automated)
    if api_cases:
        done = len(api_cases) - len(missing)
        print(f"automation: {done}/{len(api_cases)} api cases have pytest ({done * 100 // len(api_cases)}%)")
    for case_id in missing:
        warnings.append(f"{case_id}: api case has no pytest yet")
    service_tests = root / "tests" / str(service)
    if service_tests.is_dir():
        for case_id in sorted(automated_case_ids(service_tests) - case_ids):
            warnings.append(f"{case_id}: pytest marker in tests/{service} has no case in this file")
    return errors, warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--strict", action="store_true", help="treat automation gaps as errors")
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="被测项目根目录，下面有 docs/ contracts/ tests/（默认本仓库）"
    )
    args = parser.parse_args(argv)
    failed = False
    for path in args.files:
        print(f"== {path}")
        errors, warnings = check(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, root=args.root.resolve())
        for message in warnings:
            print(f"WARN  {message}")
        for message in errors:
            print(f"ERROR {message}")
        failed = failed or bool(errors) or (args.strict and bool(warnings))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
