"""check_cases.py 的校验规则，在临时目录里造最小的需求文档、契约和测试文件。"""

import copy
import json

import pytest

from scripts.check_cases import check

REQUIREMENT = "# 需求\n\n重复支付返回 `409`，订单继续保持 `paid`。\n"

VALID = {
    "service": "shop",
    "source": "docs/req.md",
    "rules": [
        {"id": "R-1", "text": "重复支付返回 409", "quote": "重复支付返回 `409`", "api": "POST /api/orders/{order_id}/pay"}
    ],
    "questions": [{"id": "Q-1", "text": "能否退款？", "status": "answered", "answer": "不能"}],
    "coverage": {
        "R-1": {
            "happy": "n/a: 由支付成功规则覆盖",
            "boundary": "n/a: 无数值边界",
            "invalid_input": "n/a: 无请求体",
            "permission": "n/a: 由跨用户规则覆盖",
            "state": "test",
            "side_effect": "n/a: 同一用例断言",
            "concurrency": "n/a: 本期不测",
        }
    },
    "test_points": [{"id": "TP-1", "rule": "R-1", "dimension": "state", "text": "paid 订单再支付"}],
    "cases": [{"id": "TC-1", "test_points": ["TP-1"], "title": "重复支付", "expected": "409", "testable": "api"}],
}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "req.md").write_text(REQUIREMENT, encoding="utf-8")
    (tmp_path / "contracts").mkdir()
    spec = {"paths": {"/api/orders/{order_id}/pay": {"post": {}}}}
    (tmp_path / "contracts" / "shop.openapi.json").write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / "tests" / "shop").mkdir(parents=True)
    (tmp_path / "tests" / "shop" / "test_pay.py").write_text('@pytest.mark.case("TC-1")\n', encoding="utf-8")
    return tmp_path


def _doc(**changes):
    doc = copy.deepcopy(VALID)
    doc.update(changes)
    return doc


def test_valid_document_passes(root):
    assert check(_doc(), root=root) == ([], [])


def test_quote_must_appear_in_requirement(root):
    rules = [{**VALID["rules"][0], "quote": "已支付订单可以退款"}]

    errors, _ = check(_doc(rules=rules), root=root)

    assert any("quote not found" in e for e in errors)


def test_api_must_exist_in_contract(root):
    rules = [{**VALID["rules"][0], "api": "POST /api/refunds"}]

    errors, _ = check(_doc(rules=rules), root=root)

    assert any("not in contract baseline" in e for e in errors)


def test_open_question_blocks(root):
    errors, _ = check(_doc(questions=[{"id": "Q-1", "text": "能否退款？", "status": "open"}]), root=root)

    assert any("open question" in e for e in errors)


def test_coverage_needs_reason_for_skipped_dimension(root):
    coverage = {"R-1": {**VALID["coverage"]["R-1"], "concurrency": "n/a:"}}

    errors, _ = check(_doc(coverage=coverage), root=root)

    assert any("R-1.concurrency" in e for e in errors)


def test_test_dimension_without_test_point_is_reported(root):
    coverage = {"R-1": {**VALID["coverage"]["R-1"], "permission": "test"}}

    errors, _ = check(_doc(coverage=coverage), root=root)

    assert any("R-1.permission is 'test' but has no test point" in e for e in errors)


def test_test_point_without_case_is_reported(root):
    points = VALID["test_points"] + [{"id": "TP-2", "rule": "R-1", "dimension": "state", "text": "x"}]

    errors, _ = check(_doc(test_points=points), root=root)

    assert "TP-2: no case" in errors


def test_non_api_case_needs_reason(root):
    cases = [{**VALID["cases"][0], "testable": "ui"}]

    errors, _ = check(_doc(cases=cases), root=root)

    assert any("needs a reason" in e for e in errors)


def test_automation_gaps_and_orphan_markers_are_warnings(root):
    cases = [{**VALID["cases"][0], "id": "TC-2"}]

    errors, warnings = check(_doc(cases=cases), root=root)

    assert errors == []
    assert "TC-2: api case has no pytest yet" in warnings
    assert any(w.startswith("TC-1: pytest marker") for w in warnings)
