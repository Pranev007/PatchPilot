# Published run artifacts

This directory is the audit trail for the numbers in the top-level README.
Everything else under `runs/` is gitignored; this one is committed on purpose.

A results table with nothing behind it is a claim. A results table whose every
row can be traced to a recorded run is evidence, and the difference costs one
directory.

## What goes here

Copy the artifacts of the run you are reporting — not every run you did:

```
runs/published/
  benchmark/                  the `patchpilot run` behind the Results table
    <org>__<repo>/
      result.json             outcome, iterations, tokens, cost, wall time
      trace.jsonl             every phase, tool call, test result, reply
    RESULTS.md
  sweep/                      the `patchpilot sweep` behind the Effort table
    manifest.json             exact settings the sweep ran with
    sweep.csv                 one row per repo run
    sweep.png                 the cost-vs-success chart
    SWEEP.md
```

To publish a run, copy it in and commit:

```bash
cp -r runs/2026-08-19T20-10-21 runs/published/benchmark
git add -f runs/published && git commit -m "Publish benchmark run artifacts"
```

## Before committing a trace

`trace.jsonl` records the agent's tool calls and summarized reasoning. It
contains nothing secret — no keys, no environment dump — but it does contain
whatever the target repository's test output printed. Skim one before
publishing, particularly if you ever extend the benchmark beyond public
repositories.

Traces also grow. A repo that runs the full six iterations can produce a few
hundred KB. If the total gets unwieldy, publish `result.json` for every repo
and `trace.jsonl` only for the ones the README actually discusses.

## Reproducing

`manifest.json` records the model, effort levels, repeats, iteration cap and
spend caps. Anyone can re-run the same configuration:

```bash
patchpilot sweep --efforts medium high xhigh --repeats 3
```

Results will not match exactly — the agent is nondeterministic, which is why
the sweep reports spread rather than a single number.
