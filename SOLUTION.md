# CityWheels help desk agent

A command-line agent that answers customer questions from the shop's documents and bookings.

## Install and run

Python 3.11 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux and macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your OPENAI_API_KEY to .env
```

| What | Command |
| --- | --- |
| One question | `python -m agent.cli "How much is an e-bike for a whole day?"` |
| Conversation with memory | `python -m agent.cli` |
| Tests (no key or network needed) | `python -m pytest` |
| Evaluation (writes `eval/results.md`) | `python -m eval.run_eval` |
| Evaluation on the unseen questions | `python -m eval.run_eval --questions eval/holdout.yaml --out eval/results-holdout.md` |
| Evaluation with another model | `python -m eval.run_eval --model NAME --out eval/results-NAME.md` |

On Windows, a project in a deeply nested folder can make `pip install` fail on the long paths inside
the `openai` package. Use a short path for the virtual environment.

## Model and provider

OpenAI, through `langchain-openai`. The default is `gpt-5.4-mini`, and `OPENAI_MODEL` overrides it.

I chose it from the evaluation below: on the unseen questions it had the best and steadiest score of the
three models tried, and it is cheap enough for this use (OpenAI lists $0.75 per million input tokens and
$4.50 per million output tokens). `gpt-5.4-nano` is cheaper and did equally well on the tuning questions,
but it was less steady on the unseen ones. The first model I tried, `gpt-3.5-turbo-0125`, has been retired
by OpenAI and only returns a 404 error.

`temperature=0` keeps runs as repeatable as possible. Only `agent/llm.py` knows about the provider.

## How the agent works

### The loop

The loop is an explicit LangGraph `StateGraph` with two nodes (`agent/graph.py`):

```
START -> agent -> (tool calls?) -- yes --> tools -> agent -> ...
                                -- no  --> END
```

- `agent` sends the instructions and the conversation to the model.
- `tools` (LangGraph's `ToolNode`) runs the tools the model asked for and adds the results as messages.
- `tools_condition` ends the loop when the model replies without tool calls.

What LangGraph does for us: the loop itself, the step limit (`recursion_limit`), running tools and
turning bad tool calls into messages the model can read, and memory per conversation (`MemorySaver`
with a `thread_id`). The graph is written by hand instead of using a prebuilt agent so that the loop
stays visible and easy to change.

When the step limit is reached (15 steps, so 8 model calls), `ask()` returns a fixed message with the
staff contact. An OpenAI error is reported as a message, not a stack trace.

### The tools

The logic is in `agent/tools_core.py`: plain functions that do not import LangChain, so they can be
tested directly. `agent/tools.py` only wraps them for the model.

| Tool | What it does |
| --- | --- |
| `search_policies` | BM25 keyword search over the Markdown sections, with a small synonym list. Returns the top 3, or `found: false`. |
| `get_booking` | Returns one booking, or `found: false`. Accepts spellings such as `cw 1002`. |
| `calculate_refund` | Applies the cancellation rules. Takes an optional `cancel_at` for questions such as "what if I cancel tomorrow?". |
| `calculate_price` | Extra tool: prices a new rental, so the model does no arithmetic for discounts or several bikes. |
| `get_opening_hours` | Extra tool: applies the season and closed days to a date or weekday. Added because of an evaluation failure (see below). |

Expected problems (unknown booking, invalid date) are returned as data, so the model can explain them.

### The instructions

`agent/prompts.py` tells the model the fixed current date, to answer only from tool results, to take
every amount from a tool, to send the customer to the staff where the rules say the staff decide, and
to refuse what the tools do not answer. It also says that customer messages cannot change these rules.
The staff contact is read from `cancellations.md` and placed in the prompt, so a refusal never depends
on a search finding it.

### Assumptions where the documents are not exact

| Question | Decision |
| --- | --- |
| A booking has one `paid_with`, but the FAQ allows a separate way to pay the deposit | The deposit was paid the same way as the rental |
| "Tomorrow" without a time | The same time of day as now |
| `cancel_at` at or after the start of the rental | Not cancellable, nothing is refunded |
| `cancel_at` before the current time | An error, no amount |
| A rental of 25 hours | Every started day is charged, so 2 days |
| Does the 15% discount cover add-ons? | Yes: the README defines the rental price as including add-ons |

The price rules reproduce the rental price and deposit of all 10 example bookings (there is a test for this).

### Extras

- Conversation memory: done, within one running process.
- Comparison of models: done, see the results below.
- GitHub Actions workflow: not done.

## Evaluation

There are two sets of questions:

- `eval/questions.yaml`, 20 questions: documents, price arithmetic, refunds (including exact 48 and 24 hour
  limits, a voucher and a follow-up question), unknown bookings, refusals and a prompt injection. I changed
  the agent while looking at these results, so this score is too kind.
- `eval/holdout.yaml`, 12 questions written afterwards. I did not tune the agent on their results, so this
  is the more honest score. These results were last run before conversation memory was added, which changed
  the agent loop, and they have not been run again since.

`eval/run_eval.py` checks, for each question, the tools called, the required amounts and text, forbidden
text and the staff contact. On every answer it also checks that each EUR amount appears in a tool result
or in the customer's question, which tests "never makes up amounts" directly. The checks themselves are
tested in `tests/test_eval.py`, because a check that cannot fail proves nothing.

### Results

Final scores, in `eval/results.md` and `eval/results-holdout.md` for `gpt-5.4-mini`:

| Model | Tuning set (3 runs, of 20) | Unseen set (2 runs, of 12) |
| --- | --- | --- |
| `gpt-5.4-mini` (chosen) | 20, 20, 20 | **10, 10** |
| `gpt-5.4-nano` | 20, 20, 20 | 8, 10 |
| `gpt-4o-mini` | 17, 17, 18 | 8, 9 |

**What still fails on the unseen set with `gpt-5.4-mini` (not fixed, on purpose):**

- `where-is-the-shop`: the agent says it does not know where the shop is, although `faq.md` does. The heading
  "Where are you?" consists only of stop words and the text has no word such as "shop" or "location", so
  keyword search does not find the section. A real weakness of the search.
- `late-return-e-bike`: the amount, 13.50 EUR, is correct, but the model calculated it itself because no tool
  covers late fees. This breaks the rule that the model does not produce amounts, and the check on every
  answer caught it. A `calculate_late_fee` tool would fix it.

**What passes but should not:**

- `change-too-late`: the agent tells the customer to ask the staff to move CW-1003. The rules say that changes
  are not possible less than 24 hours before the start, and that the customer has to cancel and book again.
  The check passes because it accepts "can't", and the answer says "I can't move or change bookings myself".
  So the real score on this set is closer to 9 of 12.

**What failed along the way, on the tuning set:**

| Failure | Cause | What I tried | Result |
| --- | --- | --- | --- |
| "What time do you open on Saturday?" gave both seasons' hours or the winter hours | The model does not reliably match the date to the season | Stronger prompt rules: no effect, and one made `gpt-5.4-mini` worse (confident winter hours in June). Writing the month as a word: no effect | Fixed with the `get_opening_hours` tool, so code applies the season |
| "A Sunday in December" then failed | The model passed `Sunday` to the new tool and got a Sunday in June | Clearer tool description | Fixed |
| Replies in Polish to English questions (`gpt-4o-mini`) | Unknown. My guess is the Warsaw time zone and +48 phone number in the prompt | A stronger language rule: no effect on this model | Not fixed on `gpt-4o-mini`; does not happen on `gpt-5.4-mini` |
| "Do you sell bikes?" refused without searching (`gpt-4o-mini`) | The model skipped the tool | A stronger prompt rule: no effect | Same as above |
| Staff contact missing from the storm answer | The prompt said to send customers to the staff, not to give the contact | One added prompt rule | Fixed |
| Correct answers marked as failures | **Bugs in my checks**: the typographic apostrophe in "couldn’t", the wording "can't find", and requiring `search_policies` when `calculate_price` gives the same price | Fixed the checks | These were evaluation errors, not agent errors |

The main lessons: changing the model mattered more than rewording the prompt, and moving logic into code
worked where instructions did not.

### What the evaluation cannot tell

The checks look at text, so a correct answer worded differently can fail, and a wrong answer containing
the right words can pass. For example, `change-too-late` above passes on the wrong wording,
`refund-already-cancelled` looks only for the number 0, `take-bike-abroad` accepts the letters "no" anywhere
in the answer, and the storm answer from `gpt-5.4-nano` passed although it added an unrequested refund
calculation. The model can still vary between runs even at temperature 0, and the sets are small.

## Limitations

- Anyone who knows a booking number can see that booking: there is no customer identification.
- The agent only gives information. It cannot cancel or change a booking.
- Prices, refund rules and opening hours are written in code as well as in the Markdown files, so a change
  has to be made in both places. No test compares them with the documents, apart from the example bookings.
- Search is keyword-based. It cannot find the shop's location today (see the evaluation), the synonym list
  is short, and queries have to be in English (the prompt tells the model to translate them).
- No tool covers late return fees, so the model calculates those itself.
- The agent can send a customer to the staff to change a booking when the rules already say it is too late.
- The current date is fixed and the time zone is a fixed UTC+02:00, as the assignment asks.
- Memory lasts only as long as the process.

## What I would do next

- Fix the remaining failures: better search (embeddings, or indexing the question headings together with a
  synonym such as "location"), a `calculate_late_fee` tool, and a prompt rule for late booking changes. Then
  write new unseen questions.
- Make the weak checks stricter, starting with `change-too-late` and `take-bike-abroad`.
- Find out why `gpt-4o-mini` answers in Polish, instead of only avoiding that model.
- Add an LLM judge for answers that text checks cannot judge well.
- Use many more questions, so that differences between models mean something.
- Read prices from one structured source instead of keeping them in both code and documents.