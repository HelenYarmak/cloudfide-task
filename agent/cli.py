"""Command line: `python -m agent.cli "question"`, or no argument for a conversation."""

from __future__ import annotations

import os
import sys
import uuid

import openai
from dotenv import load_dotenv

from agent.graph import ask, build_graph
from agent.llm import create_llm


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")
        return 1

    graph = build_graph(create_llm())
    thread_id = str(uuid.uuid4())

    try:
        if len(sys.argv) > 1:
            print(ask(graph, " ".join(sys.argv[1:]), thread_id).answer)
            return 0

        print("CityWheels help desk. Ask a question, or type 'exit' to leave.")
        while True:
            try:
                question = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            if question.lower() in {"exit", "quit"}:
                return 0
            if question:
                reply = ask(graph, question, thread_id)
                print(f"\nCityWheels: {reply.answer}")
                if reply.stopped_early:
                    # The stored history ends in unanswered tool calls: start clean.
                    thread_id = str(uuid.uuid4())
    except openai.OpenAIError as error:
        print(f"The language model could not be reached: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
