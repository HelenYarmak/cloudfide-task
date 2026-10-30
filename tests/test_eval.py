"""The evaluation is only worth something if its checks can actually fail."""

import httpx
import openai
import pytest
import yaml
from langchain_core.messages import AIMessage

from agent.graph import build_graph
from eval.run_eval import QUESTIONS_FILE, check, render_results, run_case
from tests.test_agent import FakeModel, call


def test_question_file_meets_the_assignment():
    cases = yaml.safe_load(QUESTIONS_FILE.read_text(encoding="utf-8"))
    assert len(cases) >= 10
    assert len({c["id"] for c in cases}) == len(cases)
    assert sum("calculate_refund" in c.get("tools", []) for c in cases) >= 2
    assert sum(bool(c.get("refuse")) for c in cases) >= 2


def test_check_passes_a_good_answer():
    case = {"tools": ["calculate_refund"], "amounts": [340]}
    assert check(case, "You get 340 EUR back.", ["calculate_refund"], ['"total": 340.0']) == []


def test_check_finds_a_missing_tool():
    assert check({"tools": ["get_booking"]}, "ok", [], []) == ["tool get_booking was not called"]


def test_check_finds_a_forbidden_tool():
    assert check({"forbidden_tools": ["get_booking"]}, "ok", ["get_booking"], [])


def test_amount_must_match_as_a_whole_number():
    assert check({"amounts": [340]}, "You get 3400 back.", [], [])
    assert check({"amounts": [50]}, "You get 500 back.", [], [])
    assert not check({"amounts": [340]}, "You get 340.00 back.", [], [])


def test_contains_accepts_any_of_the_options():
    case = {"contains": [["closed", "not open"]]}
    assert not check(case, "We are Not Open then.", [], [])
    assert check(case, "We are open.", [], [])


def test_contains_treats_typographic_apostrophes_as_plain_ones():
    assert not check({"contains": ["couldn't find"]}, "I couldn’t find it.", [], [])


def test_not_contains_is_a_regular_expression():
    case = {"not_contains": ["(?i)canberra"]}
    assert check(case, "It is CANBERRA.", [], [])


def test_refusal_needs_the_staff_contact():
    assert check({"refuse": True}, "I cannot help.", [], [])
    assert not check({"refuse": True}, "Ask help@citywheels.example.", [], [])


def test_made_up_amount_fails_every_case():
    failures = check({}, "You get 999 EUR back.", ["calculate_refund"], ['"total": 340.0'])
    assert failures == ["amount 999 EUR is not in any tool result"]


def test_amount_from_a_tool_or_the_customer_is_accepted():
    assert not check({}, "You get €340 back, not 500 euros.", [], ['"total": 340.0', "500 EUR?"])


def test_run_case_with_a_fake_model():
    model = FakeModel(
        [call("calculate_refund", booking_id="CW-1002"), AIMessage("You get 340 EUR.")]
    )
    case = {
        "id": "x",
        "question": "Cancel CW-1002?",
        "tools": ["calculate_refund"],
        "amounts": [340],
    }

    result = run_case(build_graph(model), case)

    assert result.passed
    assert result.tools == ["calculate_refund"]


def test_run_case_reports_a_model_error_as_a_failure():
    class BrokenModel(FakeModel):
        def _generate(self, *args, **kwargs):
            raise openai.OpenAIError("no connection")

    result = run_case(build_graph(BrokenModel([])), {"id": "x", "question": "Hi"})

    assert not result.passed
    assert "no connection" in result.failures[0]


def test_any_one_of_the_tools_is_enough():
    case = {"tools": [["search_policies", "calculate_price"]]}
    assert not check(case, "ok", ["calculate_price"], [])
    assert check(case, "ok", ["get_booking"], [])


def test_a_wrong_model_name_stops_the_run():
    class MissingModel(FakeModel):
        def _generate(self, *args, **kwargs):
            response = httpx.Response(404, request=httpx.Request("POST", "http://test"))
            raise openai.NotFoundError("no such model", response=response, body=None)

    with pytest.raises(openai.NotFoundError):
        run_case(build_graph(MissingModel([])), {"id": "x", "question": "Hi"})


def test_results_file_states_the_score():
    model = FakeModel([AIMessage("Hello | there")])
    result = run_case(build_graph(model), {"id": "x", "question": "Hi", "amounts": [1]})

    text = render_results([result], "fake-model")

    assert "0 of 1 questions passed" in text
    assert "fake-model" in text
    assert "**FAIL**" in text
