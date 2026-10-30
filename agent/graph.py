"""The agent loop as a LangGraph graph.

    START -> agent -> (tool calls?) -> tools -> agent -> ... -> END

LangGraph gives us the loop, the step limit and running the requested tools
(ToolNode).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from agent.prompts import FALLBACK_ANSWER, SYSTEM_PROMPT
from agent.tools import TOOLS

# Each model call and each round of tools is one step, so this allows about 7 rounds.
MAX_STEPS = 15


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build_graph(llm: BaseChatModel):
    model = llm.bind_tools(TOOLS)

    def agent(state: AgentState) -> dict:
        return {"messages": [model.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])]}

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


@dataclass
class AgentReply:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)  # {"name", "args"} in call order
    tool_results: list[str] = field(default_factory=list)
    stopped_early: bool = False


def ask(graph, question: str) -> AgentReply:
    """Run one customer question. Every question starts a new conversation."""
    config = {"recursion_limit": MAX_STEPS}
    try:
        state = graph.invoke({"messages": [HumanMessage(question)]}, config)
    except GraphRecursionError:
        return AgentReply(FALLBACK_ANSWER, stopped_early=True)

    messages = state["messages"]
    turn = messages[1:]  # everything after the question

    tool_calls = [
        {"name": call["name"], "args": call["args"]}
        for message in turn
        for call in getattr(message, "tool_calls", [])
    ]
    tool_results = [m.content for m in turn if isinstance(m, ToolMessage)]
    return AgentReply(messages[-1].text, tool_calls, tool_results)
