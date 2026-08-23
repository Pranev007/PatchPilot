## Results

**7 of 11 repositories migrated to a green test suite (64%)** on `claude-sonnet-5` at `--effort high`,
one run, a uniform $1.00 per-repo cap, for **$5.79**. Green means every test
that passed on Python 3.8 still passes on 3.12.

- Median cost of a successful migration: **$0.092**
- Median iterations to green: **2**
- Cost per success: **$0.83**

| Repo | Baseline | Outcome | Iters | Cost | Tools |
|---|---:|---|---:|---:|---:|
| `pallets/click` | 568 | **green** | 2 | $0.063 | 9 |
| `mahmoud/boltons` | 389 | **green** | 4 | $0.310 | 19 |
| `pallets/itsdangerous` | 297 | **green** | 2 | $0.032 | 6 |
| `pyeve/cerberus` | 242 | **green** | 3 | $0.092 | 12 |
| `pytoolz/toolz` | 203 | **green** | 3 | $0.501 | 16 |
| `scrapy/w3lib` | 144 | **green** | 2 | $0.019 | 2 |
| `john-kurkowski/tldextract` | 47 | **green** | 2 | $0.140 | 9 |
| `Delgan/loguru` | 1328 | hit spend cap | 2 | $1.032 | 33 |
| `python-attrs/attrs` | 1161 | hit spend cap | 1 | $1.029 | 35 |
| `agronholm/typeguard` | 235 | hit spend cap | 5 | $1.028 | 36 |
| `PyCQA/pycodestyle` | 54 | hit spend cap | 1 | $1.547 | 22 |

### The tool surface was worth one repository

The failure traces showed every capped repository spending 40-62% of its
calls on `run_command`, plus scratch files written and deleted, while every
success used neither. So `write_file` and `run_command` were removed, leaving
`list_files`, `read_file` and `edit_file`. The whole set was then re-run with
nothing else changed:

| | 5 tools | 3 tools |
|---|---|---|
| Repositories green | 6 | **7** |
| Total spend | $5.81 | $5.79 |
| `pytoolz/toolz` | capped, 62 calls, 2 iters | **green**, 16 calls, 3 iters |
| `agronholm/typeguard` | capped, 73 calls, 3 iters | capped, 36 calls, **5 iters** |
| `Delgan/loguru` | capped, 63 calls, 1 iter | capped, 33 calls, 2 iters |
| `python-attrs/attrs` | capped, 67 calls, 1 iter | capped, 35 calls, 1 iter |

**One more repository, for the same money.** The mechanism is in the
iteration counts rather than the totals: with no shell to explore with, the
same budget buys more attempts. `typeguard` went from 3 iterations to 5,
`loguru` from 1 to 2, and `toolz` got far enough to finish. Call counts
roughly halved across the board.

`toolz` converted on both runs under the narrowed surface, so it is not a
single lucky sample. The four that still fail are not close: `attrs` and
`pycodestyle` each still spend a whole budget inside one iteration, with
half the calls, so whatever stops them is upstream of the tool surface.

### More budget bought nothing

Before the tool change, caps of $0.30-$0.50 were raised to $1.00 -- 3.3x --
on the same 11 repositories. **Not one converted.** The same six went green
and the same five burned the full dollar. An earlier version of this README
claimed the failures were underfunded rather than beaten; that was wrong, and
raising the cap is how it was found out.

Narrowing the tool surface converted a repository that tripling the budget
could not. That contrast is the most useful thing in this table: for this
task, what the agent is allowed to do matters more than how much it is
allowed to spend.

### Costs are estimates, not invoices

Every dollar figure here is computed from published list prices in
`config.py`, not read back from a bill. Compared against the actual account
balance over ~$10 of runs, the ledger ran roughly 50% high -- the cache
accounting is the likely culprit. Treat the absolute numbers as upper bounds.
Comparisons between rows are unaffected, since every row uses the same
estimator.

The cap is also softer than it looks. It is checked before each model call,
so one expensive call can overshoot: `pycodestyle` reached $1.547 against a
$1.00 cap, 1.55x over. It bounds spend loosely; it does not pin it.

### Outcomes are reproducible; costs are not

Across repeated runs of the same set on the same model and effort, every
verdict has matched -- but the bills have not. `tldextract` cost $0.380 with
27 tool calls on one run and $0.091 with 3 on another. A single-run cost
figure is one sample; a single-run outcome has so far been stable. Two or
three runs is not enough to claim that in general, which is what `--repeats`
is for.

### What the cost distribution says

Successes were cheap -- $0.019, $0.032, $0.063, $0.092, $0.140, $0.310,
$0.501, median $0.092 -- while every failure spent its entire cap. Seven
successes cost $1.16 between them; four failures cost $4.64. When this agent
can solve a repository it does so in a median of 2 iterations and often under
10 tool calls.

The practical consequence for running agents on a budget: cap low and run
wide. A $0.30 cap would have produced six of these seven migrations for a
quarter of the money.

**Produced with `--sandbox local`, not Docker** -- the benchmark machine had no
Docker daemon. Each `result.json` records the sandbox, provider, effort and cap
it ran under, so the artifacts describe their own conditions.

Raw `result.json` and `trace.jsonl` for all 11: [`runs/published/benchmark/`](runs/published/benchmark/).
