"""check_tests.py 的检查规则：每条规则一个反例，外加一个合格的接口测试。"""

import ast
import textwrap

import pytest

from scripts.check_tests import check_function

DIMS = {"TC-1": {"side_effect"}, "TC-2": {"happy"}}


def _check(source: str):
    func = ast.parse(textwrap.dedent(source)).body[0]
    return check_function(func, DIMS)


def test_good_api_test_passes():
    errors, warnings = _check(
        """
        @pytest.mark.case("TC-1")
        def test_ok(api, db):
            response = api.post("/x")
            assert response.status_code == 201
            assert db.count("orders") == 1
        """
    )

    assert (errors, warnings) == ([], [])


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("def test_x():\n    print(1)", "no assertion"),
        ("def test_x():\n    assert True", "constant assertion"),
        ("def test_x():\n    a = 1\n    assert a == a", "constant assertion"),
        ("def test_x():\n    try:\n        f()\n    except Exception:\n        pass\n    assert f()", "exception swallowed"),
        ("def test_x():\n    try:\n        f()\n    except:\n        g()\n    assert f()", "bare except"),
        ("def test_x():\n    pytest.skip()\n    assert f()", "skip without reason"),
        ("@pytest.mark.skip\ndef test_x():\n    assert f()", "skip without reason"),
        ("def test_x(api):\n    assert api.get('/x').status_code == 200", "without @pytest.mark.case"),
        (
            "@pytest.mark.case('TC-1')\ndef test_x(api):\n    assert api.get('/x').status_code == 200",
            "does not query db",
        ),
    ],
)
def test_bad_tests_are_reported(source, message):
    errors, _ = _check(source)

    assert any(message in e for e in errors), errors


def test_pytest_raises_counts_as_assertion():
    errors, _ = _check("def test_x():\n    with pytest.raises(ValueError):\n        f()")

    assert errors == []


def test_status_code_only_is_a_warning_when_case_needs_no_db():
    errors, warnings = _check("@pytest.mark.case('TC-2')\ndef test_x(api):\n    assert api.get('/x').status_code == 200")

    assert errors == []
    assert warnings == ["only status_code is asserted"]
