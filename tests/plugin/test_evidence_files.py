"""失败证据和用例结果文件，用 pytester 在临时目录里真实跑一遍 pytest。"""

import json

FAILING_TEST = """
import pytest

@pytest.mark.case("TC-DEMO-001")
def test_fails(api_recorder):
    api_recorder.entries.append({"kind": "note", "detail": "step 1"})
    assert False
"""

PASSING_TEST = """
import pytest

@pytest.mark.case("TC-DEMO-001")
def test_passes():
    assert 1 + 1 == 2
"""


def test_failure_writes_evidence_and_case_results(pytester):
    pytester.makepyfile(test_demo=FAILING_TEST)
    out = pytester.path / "evidence"

    result = pytester.runpytest("--api-evidence-dir", str(out))

    result.assert_outcomes(failed=1)
    assert json.loads((out / "case-results.json").read_text(encoding="utf-8")) == {"TC-DEMO-001": "failed"}
    [evidence_file] = [p for p in out.glob("*.json") if p.name != "case-results.json"]
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert evidence["cases"] == ["TC-DEMO-001"]
    assert evidence["entries"] == [{"kind": "note", "detail": "step 1"}]
    result.stdout.fnmatch_lines(["*api evidence*"])


def test_next_run_clears_previous_evidence(pytester):
    out = pytester.path / "evidence"
    pytester.makepyfile(test_demo=FAILING_TEST)
    pytester.runpytest("--api-evidence-dir", str(out)).assert_outcomes(failed=1)

    pytester.makepyfile(test_demo=PASSING_TEST)
    result = pytester.runpytest("--api-evidence-dir", str(out))

    result.assert_outcomes(passed=1)
    assert sorted(p.name for p in out.glob("*.json")) == ["case-results.json"]
    assert json.loads((out / "case-results.json").read_text(encoding="utf-8")) == {"TC-DEMO-001": "passed"}
