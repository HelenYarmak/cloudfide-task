"""Runs the agent on eval/questions.yaml and writes eval/results.md.

    python -m eval.run_eval [--model NAME] [--only ID] [--questions FILE] [--out FILE]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import openai
import yaml
from dotenv import load_dotenv

from agent.graph import AgentReply, ask, build_graph

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_FILE = EVAL_DIR / "questions.yaml"
RESULTS_FILE = EVAL_DIR / "results.md"
STAFF_CONTACT = ("help@citywheels.example", "+48 000 000 000")
EUR_AMOUNT = re.compile(
    r"(?:€|EUR)\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:€|EUR\b|euros?\b|евро)", re.I
)


@dataclass
class CaseResult:
    id: str
    questions: list[str]
    answer: str
    tools: list[str]
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text)}


def check(case: dict, answer: str, tools: list[str], sources: list[str]) -> list[str]:
    """Compare one answer with the expected outcome. Returns what went wrong.

    `sources` is the text the answer may take amounts from: tool results and questions.
    """
    failures = []
    # Models often write typographic apostrophes: "couldn’t".
    lowered = answer.lower().replace("’", "'")

    for expected in case.get("tools", []):
        options = expected if isinstance(expected, list) else [expected]
        if not set(options) & set(tools):
            failures.append(f"tool {' or '.join(options)} was not called")
    for tool in case.get("forbidden_tools", []):
        if tool in tools:
            failures.append(f"tool {tool} must not be called")

    for amount in case.get("amounts", []):
        if float(amount) not in _numbers(answer):
            failures.append(f"amount {amount} is missing")

    for expected in case.get("contains", []):
        options = expected if isinstance(expected, list) else [expected]
        if not any(option.lower() in lowered for option in options):
            failures.append(f"none of {options} found")

    for pattern in case.get("not_contains", []):
        if re.search(pattern, answer):
            failures.append(f"forbidden text found: {pattern}")

    if case.get("refuse") and not any(contact in answer for contact in STAFF_CONTACT):
        failures.append("the staff contact is missing")

    # The rule "never make up amounts", checked on every answer.
    known = _numbers(" ".join(sources))
    for groups in EUR_AMOUNT.findall(answer):
        amount = groups[0] or groups[1]
        if float(amount.replace(",", ".")) not in known:
            failures.append(f"amount {amount} EUR is not in any tool result")

    return failures


def run_case(graph, case: dict) -> CaseResult:
    questions = case["question"] if isinstance(case["question"], list) else [case["question"]]
    tools: list[str] = []
    sources = list(questions)
    reply = AgentReply("")
    try:
        for question in questions:
            reply = ask(graph, question, thread_id=case["id"])
            sources += reply.tool_results
        # Only the tools of the last question count, so a follow-up has to call them again.
        tools = [call["name"] for call in reply.tool_calls]
        failures = check(case, reply.answer, tools, sources)
    except (openai.AuthenticationError, openai.NotFoundError):
        raise  # A wrong key or model name says nothing about the agent: stop the whole run.
    except openai.OpenAIError as error:
        failures = [f"model error: {error}"]
    return CaseResult(case["id"], questions, reply.answer, tools, failures)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_results(results: list[CaseResult], model: str) -> str:
    passed = sum(r.passed for r in results)
    lines = [
        "# Evaluation results",
        "",
        f"**{passed} of {len(results)} questions passed.**",
        "",
        f"- Model: `{model}`, temperature 0",
        f"- Date of the run: {date.today().isoformat()}",
        "- Command: `python -m eval.run_eval`",
        "",
        "| Result | Question | Tools called | What went wrong |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        tools = ", ".join(r.tools) or "none"
        lines.append(
            f"| {'pass' if r.passed else '**FAIL**'} | `{r.id}` | {tools} "
            f"| {_cell('; '.join(r.failures))} |"
        )
    lines += ["", "## Answers", ""]
    for r in results:
        lines += [f"### {r.id}", ""]
        lines += [f"> {_cell(q)}" for q in r.questions]
        lines += ["", r.answer or "(no answer)", ""]
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="model name; default: OPENAI_MODEL")
    parser.add_argument("--only", help="run only the question with this id")
    parser.add_argument("--questions", type=Path, default=QUESTIONS_FILE)
    parser.add_argument("--out", type=Path, default=RESULTS_FILE)
    args = parser.parse_args()

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")
        return 1

    from agent.llm import create_llm

    llm = create_llm(args.model)
    graph = build_graph(llm)
    cases = yaml.safe_load(args.questions.read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]

    results = []
    try:
        for case in cases:
            result = run_case(graph, case)
            results.append(result)
            status = "pass" if result.passed else "FAIL"
            print(f"{status}  {result.id}  {'; '.join(result.failures)}")
    except (openai.AuthenticationError, openai.NotFoundError) as error:
        print(f"Stopped, the results file was not changed: {error}")
        return 1

    args.out.write_text(render_results(results, llm.model_name), encoding="utf-8")
    print(f"\n{sum(r.passed for r in results)} of {len(results)} passed. Saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
