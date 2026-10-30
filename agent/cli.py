"""Command line: `python -m agent.cli "question"`, or no argument to ask several questions."""

from __future__ import annotations

import os
import sys

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

    try:
        if len(sys.argv) > 1:
            print(ask(graph, " ".join(sys.argv[1:])).answer)
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
                print(f"\nCityWheels: {ask(graph, question).answer}")
    except openai.OpenAIError as error:
        print(f"The language model could not be reached: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
