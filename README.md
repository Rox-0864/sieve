# Sieve

> Turns raw community conversations into publish-ready content.

Community teams lose their best material in the scrollback. A student lands their first
job in `#logros-y-empleos` and it's gone in a week. Someone asks the exact question three
people were going to ask. The words that make a good LinkedIn post already exist —
nobody has time to go find them.

Sieve reads the conversation, decides which moments are worth surfacing, and turns them
into finished assets: a LinkedIn post, a newsletter highlight, a FAQ entry.

**A sieve doesn't invent anything. It lets the valuable things through.**

---

## Architecture

Seven stages. Each one is a separate module, separately testable, and none of them knows
anything about the next one's internals.

```
   ① INGEST ──────►  ② NORMALIZE ──────►  ③ ANALYZE ──────►  ④ SCORE
   JSON/CSV        Pydantic schema      LLM: sentiment     Weighted formula
   Webhook         dedupe, validate     topics, signals    (explainable)
                                    │
                                    ▼
   ⑦ PUBLISH ◄── ⑥ GENERATE ◄── ⑤ ROUTER ◄──────────────────┘
   OCI Object      one prompt per      conditional
   Storage         channel + few-shot  branch
```

**Why domain logic lives in Python, not in n8n.** n8n is the trigger and the glue
(`Webhook → HTTP → IF → OCI → Response`). The logic lives in Python because logic needs
diffs, unit tests, and stack traces. A workflow is a single opaque JSON blob: you cannot
review it, and you cannot test the conditional branch. The project brief explicitly
allows `n8n, Python, or equivalents` — this takes that option and keeps the repo legible.

---

## The scoring problem, and how it gets solved

Without ranking, the output reads like a robot wrote it. The formula is deliberately
simple and **deliberately explainable**:

```
score = 0.30 · sentiment          LLM analysis
      + 0.25 · topic salience     how often the theme recurs in the batch
      + 0.20 · recency            exponential decay
      + 0.15 · achievement signal  "hired", "first job", "graduated"…
      + 0.10 · engagement signal  length, questions, reaction count
```

Explainable beats clever. In a demo you can point at a message and say *"it scored 0.82
because it's a hiring announcement and the theme 'Contratación' appears three times in
this batch."* That's an argument from a product owner, not from an engineer.

The router then branches on it:

| Condition | Asset |
|---|---|
| `score ≥ 0.65` **and** achievement signal high | `caso_exito` → LinkedIn post + newsletter block |
| looks like a question **and** `score ≥ 0.35` | `faq` → tutorial / tip |
| `score < 0.35` | `highlight` → weekly community summary |

Questions are detected with a cheap lexical heuristic, not the LLM. The model spends
tokens on judgment; it doesn't spend them on `str.endswith("?")`.

---

## The contract

`src/sieve/models.py` is the center of the project. The ONE brief defines an exact JSON
input and output shape; if the models don't validate against it, the deliverable isn't
met no matter how good the generated copy is.

Pydantic turns that document into something the interpreter enforces at runtime:

```python
from sieve import validar_contrato_documento

respuesta = validar_contrato_documento(payload)   # raises if the shape is wrong
```

`tests/test_contrato.py` validates against the brief's own JSON payloads, character for
character. If the contract changes, the test breaks and names the field.

---

## Status

| Milestone | What it produces | Done |
|---|---|---|
| **M0** Foundations | repo, structure, the Pydantic contract | ✅ |
| **M1** Ingestion | JSON/CSV loaders, validated | |
| **M2** Analysis | LLM sentiment + topics (provider-swappable) | |
| **M3** Scoring & router | weighted formula + branches, with tests | |
| **M4** Assets | 3 formats, few-shot prompts, validated JSON | |
| **M5** OCI | bucket, PAR, scoped IAM policy | |
| **M6** Interface | Streamlit curation panel with approve/reject | |
| **M7** Orchestration | n8n flow on top of the API | |
| **M8** Polish | README, diagram, demo, `v1.0` tag | |

Each milestone ends with something that runs end to end. No milestone is "80% of the
module works."

---

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # then fill in your API keys

pytest                    # the contract tests
```

---

## Data policy

**No real community data ever enters this repository.**

Discord and Slack messages are personal data belonging to identifiable people. Pushing
them to a third-party LLM API is processing without consent or legal basis, and
publishing them is worse. The dataset here is `data/samples/`, entirely synthetic.

Where the project uses real public data to validate the scoring, it is anonymized,
openly licensed, and attributed. See [`ATTRIBUTION.md`](ATTRIBUTION.md).

---

## Stack

Python 3.11 · Pydantic v2 · provider-agnostic LLM adapter (Gemini / OpenAI / Claude /
Ollama) · OCI Object Storage (Always Free) · Streamlit · n8n · pytest · Ruff · mypy

---

## License

Code: MIT. Third-party data: see [`ATTRIBUTION.md`](ATTRIBUTION.md).
