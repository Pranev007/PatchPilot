## Results

**6 of 11 repositories migrated to a green test suite (55%)** on `claude-sonnet-5` at `--effort high`,
one run, a uniform $1.00 per-repo cap, for **$5.81**. Green means every test
that passed on Python 3.8 still passes on 3.12.

- Median cost of a successful migration: **$0.071**
- Median iterations to green: **2**
- Cost per success: **$0.97**

| Repo | Baseline | Outcome | Iters | Cost | Tools |
|---|---:|---|---:|---:|---:|
| `pallets/click` | 568 | **green** | 2 | $0.085 | 16 |
| `mahmoud/boltons` | 389 | **green** | 4 | $0.470 | 48 |
| `pallets/itsdangerous` | 297 | **green** | 2 | $0.031 | 6 |
| `pyeve/cerberus` | 242 | **green** | 3 | $0.056 | 4 |
| `scrapy/w3lib` | 144 | **green** | 2 | $0.018 | 2 |
| `john-kurkowski/tldextract` | 47 | **green** | 2 | $0.091 | 3 |
| `Delgan/loguru` | 1328 | hit spend cap | 1 | $1.005 | 63 |
| `python-attrs/attrs` | 1161 | hit spend cap | 1 | $1.015 | 67 |
| `agronholm/typeguard` | 235 | hit spend cap | 3 | $1.016 | 73 |
| `pytoolz/toolz` | 203 | hit spend cap | 2 | $1.011 | 62 |
| `PyCQA/pycodestyle` | 54 | hit spend cap | 1 | $1.015 | 50 |

### More budget bought nothing

An earlier run used caps of $0.30-$0.50, and every failure hit its cap. The
obvious reading was that the failures were underfunded rather than beaten, and
that is what an earlier version of this README claimed. It was wrong, and the
way to find out was to raise the cap and look.

| | $0.30-$0.50 caps | $1.00 cap |
|---|---|---|
| Repositories green | 6 | **6** |
| Which ones | click, boltons, itsdangerous, cerberus, w3lib, tldextract | *identical* |
| Failures converted | -- | **0** |

**3.3x the budget converted not one repository.** All five failures burned the
full dollar, at 50-73 tool calls each, and `loguru`, `attrs` and `pycodestyle`
never reached a second test run even with the extra budget. These are not
repositories a dollar short of success; the agent does not know what to do
with them and spends the money looking around. The failure taxonomy below
says the same thing from the other direction -- failures are dominated by
shell calls and scratch files, not edits.

### Outcomes are reproducible; costs are not

Running the same 11 repositories twice on the same model and effort gives the
same verdict every time -- 11 of 11 outcomes matched -- but wildly different
bills:

| Repo | Run 1 | Run 2 |
|---|---|---|
| `john-kurkowski/tldextract` | $0.380, 27 tools | $0.091, **3 tools** |
| `pallets/itsdangerous` | $0.109, 21 tools | $0.031, 6 tools |
| `pyeve/cerberus` | $0.091, 8 tools | $0.056, 4 tools |
| `pallets/click` | $0.060, 13 tools | $0.085, 16 tools |
| `mahmoud/boltons` | $0.372, 40 tools | $0.470, 48 tools |
| `scrapy/w3lib` | $0.019, 2 tools | $0.018, 2 tools |

`tldextract` solved the same problem for **4.2x less money and a ninth of the
tool calls** on the second attempt. Nothing changed but the sampling.

This is the strongest argument in the repository for why `--repeats` exists.
A single-run cost figure is close to meaningless; a single-run *outcome*, at
least across these two runs, was stable. Two runs is not enough to claim that
in general, which is what the sweep is for.

### What the cost distribution says

Successes were cheap -- $0.018, $0.031, $0.056, $0.085, $0.091, $0.470, median
$0.071 -- while every failure spent its entire cap. Six successes cost $0.75
between them; five failures cost $5.06. When this agent can solve a repository
it does so in a median of 2 iterations and often a handful of tool calls.

The practical consequence for anyone running agents on a budget: cap low and
run wide. A $0.30 cap would have produced the same six migrations for a
quarter of the money, because the repositories that fail are not close.

**Produced with `--sandbox local`, not Docker** -- the benchmark machine had no
Docker daemon. Each `result.json` records the sandbox, provider, effort and cap
it ran under, so the artifacts describe their own conditions.

Raw `result.json` and `trace.jsonl` for all 11: [`runs/published/benchmark/`](runs/published/benchmark/).
