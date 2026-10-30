"""Tests of the agent loop with a fake model: no network and no API key."""

from itertools import count
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.graph import ask, build_graph
from agent.prompts import FALLBACK_ANSWER


def call(name, **args):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call_{name}"}])


class FakeModel(BaseChatModel):
    """Plays back prepared replies and records what it was sent."""

    replies: Any
    seen: list = []
    bound_tools: list = []

    def __init__(self, replies):
        super().__init__(replies=iter(replies), seen=[], bound_tools=[])

    @property
    def _llm_type(self):
        return "fake"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.extend(t.name for t in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=next(self.replies))])


def test_loop_runs_the_tool_and_gives_the_result_back_to_the_model():
    model = FakeModel(
        [call("calculate_refund", booking_id="CW-1002"), AIMessage("You get 340 EUR.")]
    )

    reply = ask(build_graph(model), "Cancel CW-1002?")

    assert reply.answer == "You get 340 EUR."
    assert reply.tool_calls == [{"name": "calculate_refund", "args": {"booking_id": "CW-1002"}}]
    assert len(model.seen) == 2
    tool_message = model.seen[1][-1]
    assert isinstance(tool_message, ToolMessage)
    assert '"total_refund_eur": 340.0' in tool_message.content


def test_answer_without_tools_ends_the_loop_at_once():
    model = FakeModel([AIMessage("Please contact the staff.")])

    reply = ask(build_graph(model), "What is the capital of Australia?")

    assert reply.answer == "Please contact the staff."
    assert reply.tool_calls == []
    assert len(model.seen) == 1


def test_several_tools_in_a_row():
    model = FakeModel(
        [
            call("get_booking", booking_id="CW-1001"),
            call("search_policies", query="cancel"),
            AIMessage("Done."),
        ]
    )

    reply = ask(build_graph(model), "Tell me about CW-1001")

    assert [c["name"] for c in reply.tool_calls] == ["get_booking", "search_policies"]
    assert len(reply.tool_results) == 2


def test_unknown_booking_reaches_the_model_as_not_found():
    model = FakeModel([call("get_booking", booking_id="CW-9999"), AIMessage("Not found.")])

    reply = ask(build_graph(model), "Where is CW-9999?")

    assert reply.answer == "Not found."
    assert '"found": false' in reply.tool_results[0]


def test_model_gets_the_instructions_and_the_tools():
    model = FakeModel([AIMessage("Hi.")])

    ask(build_graph(model), "Hello")

    system = model.seen[0][0]
    assert isinstance(system, SystemMessage)
    assert "10 June 2026, 09:00" in system.content
    assert "help@citywheels.example" in system.content
    assert set(model.bound_tools) >= {"search_policies", "get_booking", "calculate_refund"}


def test_a_tool_that_does_not_exist_does_not_crash():
    model = FakeModel([call("delete_everything"), AIMessage("Sorry.")])

    reply = ask(build_graph(model), "Do it")

    assert reply.answer == "Sorry."
    assert "not a valid tool" in reply.tool_results[0]


def test_invalid_tool_arguments_do_not_crash():
    model = FakeModel([call("get_booking"), AIMessage("Which booking?")])

    reply = ask(build_graph(model), "My booking?")

    assert reply.answer == "Which booking?"
    assert "booking_id" in reply.tool_results[0]


def test_a_model_that_never_stops_is_cut_off():
    # A new message every time: add_messages replaces a message whose id it already has.
    model = FakeModel(call("search_policies", query="hours") for _ in count())

    reply = ask(build_graph(model), "Hours?")

    assert reply.stopped_early is True
    assert reply.answer == FALLBACK_ANSWER
    assert len(model.seen) == 8


def test_messages_of_one_thread_are_remembered():
    model = FakeModel([AIMessage("First."), AIMessage("Second.")])
    graph = build_graph(model)

    ask(graph, "About CW-1002", thread_id="a")
    ask(graph, "And tomorrow?", thread_id="a")

    questions = [m.content for m in model.seen[1] if isinstance(m, HumanMessage)]
    assert questions == ["About CW-1002", "And tomorrow?"]


def test_threads_do_not_share_memory():
    model = FakeModel([AIMessage("First."), AIMessage("Second.")])
    graph = build_graph(model)

    ask(graph, "About CW-1002", thread_id="a")
    ask(graph, "Someone else", thread_id="b")

    questions = [m.content for m in model.seen[1] if isinstance(m, HumanMessage)]
    assert questions == ["Someone else"]


def test_reply_reports_only_the_tools_of_the_last_question():
    model = FakeModel(
        [call("get_booking", booking_id="CW-1001"), AIMessage("One."), AIMessage("Two.")]
    )
    graph = build_graph(model)

    ask(graph, "First", thread_id="a")
    reply = ask(graph, "Second", thread_id="a")

    assert reply.tool_calls == []
