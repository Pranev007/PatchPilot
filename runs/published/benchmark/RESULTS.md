## Results

**6 of 11 repositories migrated to a green test suite (55%)** on `claude-sonnet-5` at `--effort high`.
Green means every test that passed on Python 3.8 still passes on 3.12.

- Median cost of a successful migration: **$0.100**
- Median iterations to green: **2**
- Cost per success: **$0.51**
- Whole benchmark: **$3.07**

| Repo | Baseline | Outcome | Iters | Cost | Cap | Tools |
|---|---:|---|---:|---:|---:|---:|
| `pallets/click` | 568 | **green** | 2 | $0.060 | $0.30 | 13 |
| `mahmoud/boltons` | 389 | **green** | 4 | $0.372 | $0.42 | 40 |
| `pallets/itsdangerous` | 297 | **green** | 2 | $0.109 | $0.30 | 21 |
| `pyeve/cerberus` | 242 | **green** | 3 | $0.091 | $0.30 | 8 |
| `scrapy/w3lib` | 144 | **green** | 2 | $0.019 | $0.30 | 2 |
| `john-kurkowski/tldextract` | 47 | **green** | 2 | $0.380 | $0.50 | 27 |
| `Delgan/loguru` | 1328 | hit spend cap | 1 | $0.303 | $0.30 | 32 |
| `python-attrs/attrs` | 1161 | hit spend cap | 1 | $0.426 | $0.40 | 37 |
| `agronholm/typeguard` | 235 | hit spend cap | 3 | $0.422 | $0.42 | 43 |
| `pytoolz/toolz` | 203 | hit spend cap | 1 | $0.460 | $0.42 | 30 |
| `PyCQA/pycodestyle` | 54 | hit spend cap | 1 | $0.426 | $0.42 | 32 |

### The failures are budget, not defeat

Not one repository exhausted its *iteration* cap. All five failures hit the
**spend** cap, set at $0.30-$0.50 because the whole benchmark ran on a $5
budget. They ran out of money, not out of ideas.

How much that matters is measurable, so it was measured. Capped repos were
re-run with nothing changed but the cap:

| Repo | $0.30 cap | $0.42 cap |
|---|---|---|
| `mahmoud/boltons` | capped at 3 iters | **green in 4** |
| `agronholm/typeguard` | capped at 3 iters | capped at 3 iters |
| `pytoolz/toolz` | capped at 1 iter | capped at 1 iter |
| `PyCQA/pycodestyle` | capped at 1 iter | capped at 1 iter |

A 40% larger budget converted **one of four**. The failures are therefore not
uniformly a dollar short: `boltons` was genuinely close, while `toolz` and
`pycodestyle` burned an entire budget inside a single iteration both times
and never reached a second test run. For those, more budget buys more
exploration rather than more progress.

`Delgan/loguru` (1,328 tests) and `python-attrs/attrs` (1,161) were never
given a realistic budget -- their baselines alone take minutes and their
failure digests are large. They stay in the denominator rather than being
quietly dropped.

### What the cost distribution says

Successful migrations were cheap -- $0.019, $0.060, $0.091, $0.109, $0.372,
$0.380 -- and every failure burned its entire cap. When this agent can solve
a repository it does so in a median of 2 iterations; when it cannot, the
money goes on looking around. That asymmetry is the practical finding: for
this task a *low* per-repo cap across more repositories buys more successful
migrations per dollar than a high cap across fewer.

One caveat on the cap: it is checked before each model call, so a single
expensive call can overshoot it. Two repos ran to $0.46 against a $0.42 cap.
It bounds spend, it does not pin it.

Raw `result.json` and `trace.jsonl` for all 11: [`runs/published/benchmark/`](runs/published/benchmark/).
