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

## Ingestion (M1)

Ingestion is the stage where a project like this usually dies, and always for the same
reason: the moment it needs the internet. So the rule here is structural.

**Every test in this repository runs with the network blocked.** Not "uses fixtures" —
`socket.connect` is replaced in `tests/conftest.py` and raises if anything tries to open
a connection. A test that silently reaches a third-party API is not a test, it's a
landmine: it passes on your machine, it fails in CI, and it can burn a rate limit that
nobody budgeted for.

That guard exists because of a bug it now prevents. A test reconstructed the cache key by
hand, it didn't match what the client generated, and instead of failing it quietly
downloaded 100 real questions. The fix was architectural, not a patch: the HTTP call sits
behind an injectable transport, so an offline test is a constructor argument instead of a
promise in the docs.

```python
from sieve.ingestion import ClienteStackExchange, clave_de_preguntas

# Pre-warm once, from a machine with network. Reads 1 request, not 100.
cliente = ClienteStackExchange()
cliente.preguntas(paginas=1, minimo_score=1)

# Everything after that is served from disk.
cliente.preguntas(paginas=1, minimo_score=1)
```

### Why the API and not the dump

`es.stackoverflow` is available as a 528 MB compressed dump. This project never
downloads it. The API returns the same fields as JSON over HTTP, costs zero disk, and the
brief's machine has 5 GB free at 96% capacity. The dump would fill it for data the API
gives away for free. It would also be a bad look in a portfolio repo: 528 MB of scraped
questions sitting next to a claim about respecting data sources.

The one hard constraint the API imposes is **300 requests per day**, advertised in the
`quota_remaining` response header. So the client is cache-first, tracks its own quota, and
`--cache-only` will refuse to touch the network rather than spend a request you didn't
mean to.

### Ground truth is not a feature

`Interaccion` is what someone said. `GroundTruth` is what the platform's other humans
concluded about it — upvotes, reply count, and `accepted_answer_id`. They are separate
types on purpose, because conflating them is the mistake that makes a ranking system
unfalsifiable: if the product reads the score column, it is grading its own homework.

`GroundTruth` is **evaluation-only**. The product never consumes it. What it buys is the
other half of the question — not *"is my score pretty"* but *"does my score rank things
the way people do"*. `accepted_answer_id` is about as honest a human judgment as an API
gives you, and only 43% of Spanish Stack Overflow questions have one, which makes those
valuable precisely because they are scarce.

`GroundTruth.intensidad()` treats acceptance as a **floor, not a weight**. A question
whose answer a human accepted beats a question with 100 upvotes and no accepted answer,
no matter how the remaining terms are arranged. Getting that backwards would rank by
popularity while claiming to rank by value.

### CLI

```bash
sieve ingest data/samples/lote_demo.json          # synthetic, offline
sieve ingest exports/chat.csv --salida out.json   # CSV, delimitador autodetectado
sieve fetch-se --paginas 2 --cache-only           # reads cache or fails, never the network
sieve cache --limpiar
```

CSV delimiters are auto-detected because a comma-separated file exported by Excel in a
Spanish locale uses `;`, and parsing that with a comma produces a single column named
`autor;canal;texto` — a failure that surfaces three stages later, when the LLM reports
that nobody has an author.

---

## Status

| Milestone | What it produces | Done |
|---|---|---|
| **M0** Foundations | repo, structure, the Pydantic contract | ✅ |
| **M1** Ingestion | JSON/CSV loaders, Stack Exchange source, offline cache | ✅ |
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

pytest                    # contract + ingestion, fully offline
mypy src/sieve            # strict
ruff check .
```

---

## Data policy

**No real community data from private platforms ever enters this repository.**

Discord and Slack messages are personal data belonging to identifiable people. Pushing
them to a third-party LLM API is processing without consent or legal basis, and
publishing them is worse. The dataset in `data/samples/` is entirely synthetic — fictional
people, hand-written messages.

The one live source is the Stack Exchange API, on `es.stackoverflow`, because its content
is openly licensed and its API carries the author's name, a profile link, the post link,
and the per-post license in the response. All four are stored, and
[`ATTRIBUTION.md`](ATTRIBUTION.md) is a real file, not a promise.

---

## Stack

Python 3.11 · Pydantic v2 · provider-agnostic LLM adapter (Gemini / OpenAI / Claude /
Ollama) · OCI Object Storage (Always Free) · Streamlit · n8n · pytest · Ruff · mypy

---

## License

Code: MIT. Third-party data: see [`ATTRIBUTION.md`](ATTRIBUTION.md).
