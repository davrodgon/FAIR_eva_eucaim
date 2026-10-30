import pytest

from fair_eva.api.evaluator import EvaluatorBase

DOI = "10.1234/abcd.5678"


class DummyEvaluator(EvaluatorBase):
    """Minimal concrete evaluator, bypassing the plugin configuration."""

    def __init__(self):
        pass

    def get_metadata(self):
        return None


@pytest.fixture
def evaluator():
    return DummyEvaluator()


def test_evaluator_base_is_abstract():
    with pytest.raises(TypeError):
        EvaluatorBase("id", api_endpoint="http://example.org")


def test_eval_persistency_persistent(evaluator):
    points, msgs = evaluator.eval_persistency([DOI])
    assert points == 100
    assert msgs[0]["points"] == 100
    assert DOI in msgs[0]["message"]


def test_eval_persistency_not_persistent(evaluator):
    points, msgs = evaluator.eval_persistency(["internal-id"])
    assert points == 0
    assert msgs[0]["points"] == 0


def test_eval_persistency_mixed(evaluator):
    points, msgs = evaluator.eval_persistency(["internal-id", DOI])
    assert points == 100
    assert [m["points"] for m in msgs] == [0, 100]


def test_eval_persistency_empty(evaluator):
    assert evaluator.eval_persistency([]) == (0, [])


def test_eval_uniqueness(evaluator):
    assert evaluator.eval_uniqueness([DOI])[0] == 100
    assert evaluator.eval_uniqueness(["internal-id"])[0] == 0


def test_eval_uniqueness_custom_label(evaluator):
    _, msgs = evaluator.eval_uniqueness([DOI], data_or_metadata="data")
    assert "data" in msgs[0]["message"]
