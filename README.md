# PatchPilot — Test-Verified Dependency Migration

[![tests](https://github.com/Pranev007/PatchPilot/actions/workflows/tests.yml/badge.svg)](https://github.com/Pranev007/PatchPilot/actions/workflows/tests.yml)

An agentic system that upgrades Python packages from 3.8 to 3.12 and **proves it
worked** — the repository's own test suite is the acceptance criterion, not the
model's opinion of its own patch.

> **7 of 11 real open-source repositories** migrated to a green test suite,
> for **$5.79**, at a median of **$0.092** and **2 iterations** per success.
> Every result is reproducible from committed artifacts.

## Architecture

- **Agent:** hand-written tool-use loop over the Claude API — three tools, no shell
- **Oracle:** `pytest` per-test outcomes, compared as *sets of node IDs* before and after
- **Sandbox:** a fresh `uv` virtualenv per repository, interpreter asserted after setup
- **Providers:** Anthropic, plus any OpenAI-compatible endpoint — Gemini, DeepSeek, OpenRouter, Together, HuggingFace, OpenAI
- **Instrumentation:** per-repo ledger of tokens, dollars, iterations, and a full JSONL trace

## How It Works

```
clone repo @ pinned ref
      ↓
venv on 3.8  →  install  →  pytest  →  BASELINE (set of passing test IDs)
      ↓
swap venv to 3.12   (same checkout — only the interpreter changes)
      ↓
  ┌── install → pytest
  │       ↓
  │   regressions vs baseline?  ── no ──→  GREEN
  │       │ yes
  │   hand the failures to the agent; it edits files
  └───────┘                      (capped by iterations and by dollars)
```

A repository counts as migrated only if **every test that passed on 3.8 still
passes on 3.12**. Comparing sets rather than totals means pre-existing failures
never count against the agent.

## How to Run Locally

### 1. Install

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -e ".[dev,openai,plot]"
```

### 2. Set a provider key

```bash
setx ANTHROPIC_API_KEY "your-key"
```

Any of `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `HF_TOKEN` or `OPENAI_API_KEY` works with the matching `--provider`. Keys are read from the environment, never passed as arguments.

### 3. Check the environment

```bash
patchpilot doctor
```

### 4. Validate the benchmark set — free

```bash
patchpilot run --max-iterations 1
```

Establishes a 3.8 baseline and runs the suite once on 3.12 **without ever calling the model**. Costs $0.00.

### 5. Run it

```bash
patchpilot run --model claude-sonnet-5 --effort high --max-spend 1.00
```

> ⚠️ Repositories are built and tested on your machine with no isolation — `pip install -e .` executes their `setup.py`. Only run repositories you have read.

## Results

| Repo | Baseline | Outcome | Iters | Cost |
|---|---:|---|---:|---:|
| `pallets/click` | 568 | **green** | 2 | $0.063 |
| `mahmoud/boltons` | 389 | **green** | 4 | $0.310 |
| `pallets/itsdangerous` | 297 | **green** | 2 | $0.032 |
| `pyeve/cerberus` | 242 | **green** | 3 | $0.092 |
| `pytoolz/toolz` | 203 | **green** | 3 | $0.501 |
| `scrapy/w3lib` | 144 | **green** | 2 | $0.019 |
| `john-kurkowski/tldextract` | 47 | **green** | 2 | $0.140 |
| `Delgan/loguru` | 1328 | spend cap | 2 | $1.032 |
| `python-attrs/attrs` | 1161 | spend cap | 1 | $1.029 |
| `agronholm/typeguard` | 235 | spend cap | 5 | $1.028 |
| `PyCQA/pycodestyle` | 54 | spend cap | 1 | $1.547 |

`claude-sonnet-5`, `--effort high`, uniform $1.00 cap. Raw `result.json` and `trace.jsonl` for all 11 in [`runs/published/`](runs/published/).

## Features

- **Test-verified, not vibes.** A migration counts only when the suite says so.
- **Honest outcome taxonomy.** "Could not build", "no baseline", "out of budget" and "failed" are four different results, never collapsed into one number.
- **Cost ledger.** Tokens, dollars, iterations and tool calls recorded per repository, with enforced spend caps.
- **Full traces.** Every tool call and test run written to JSONL — the failure analysis is built from them.
- **Provider-agnostic.** Seven backends behind one interface; the verify loop does not care who wrote the patch.
- **Free dry-run mode.** The whole benchmark set can be validated without spending anything.

## Three Things I Found

**Narrowing the tool surface beat raising the budget.** Tripling the spend cap converted zero repositories. Removing the shell tools converted one, for the same money — the failure traces showed 40–62% of calls going to `run_command` instead of edits.

**The harness had six bugs, all found by running it.** Two false greens, a false failure, a sandbox that silently used the wrong interpreter, and an oracle that disagreed with itself between runs. Three of them produced confident numbers that meant nothing.

**"The tests pass" is a narrower claim than it sounds.** The same repository produced two accepted patches — one fixing the dependency, one only silencing the warning — and the oracle scored them identically.

📄 **[Full findings, methodology and limitations →](FINDINGS.md)**

## Tech Stack

`Python 3.11+` · `Claude API` · `pytest` · `uv` · `PyYAML` · `matplotlib` · `GitHub Actions`
