"""The agent loop as a LangGraph graph.

    START -> agent -> (tool calls?) -> tools -> agent -> ... -> END

LangGraph gives us the loop, the step limit, running the requested tools
(ToolNode) and the per-conversation memory (the checkpointer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
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
    return builder.compile(checkpointer=MemorySaver())


@dataclass
class AgentReply:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)  # {"name", "args"} in call order
    tool_results: list[str] = field(default_factory=list)
    stopped_early: bool = False


def ask(graph, question: str, thread_id: str = "default") -> AgentReply:
    """Run one customer question. Messages of the same `thread_id` share a memory."""
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_STEPS}
    try:
        state = graph.invoke({"messages": [HumanMessage(question)]}, config)
    except GraphRecursionError:
        return AgentReply(FALLBACK_ANSWER, stopped_early=True)

    messages = state["messages"]
    last_question = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    turn = messages[last_question + 1 :]

    tool_calls = [
        {"name": call["name"], "args": call["args"]}
        for message in turn
        for call in getattr(message, "tool_calls", [])
    ]
    tool_results = [m.content for m in turn if isinstance(m, ToolMessage)]
    return AgentReply(messages[-1].text, tool_calls, tool_results)
