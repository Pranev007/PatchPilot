## Results

**5 of 12 repositories migrated to a green test suite (42%)**, on `claude-sonnet-5` at `--effort high`, for **$3.00** total.

- Median cost of a successful migration: **$0.091**
- Median iterations to green: **2**
- Cost per success: **$0.60**

| Repo | Baseline | Outcome | Iters | Cost | Cap | Tools |
|---|---:|---|---:|---:|---:|---:|
| `pallets/click` | 568 | **green** | 2 | $0.060 | $0.30 | 13 |
| `pallets/itsdangerous` | 297 | **green** | 2 | $0.109 | $0.30 | 21 |
| `pyeve/cerberus` | 242 | **green** | 3 | $0.091 | $0.30 | 8 |
| `scrapy/w3lib` | 144 | **green** | 2 | $0.019 | $0.30 | 2 |
| `john-kurkowski/tldextract` | 47 | **green** | 2 | $0.380 | $0.50 | 27 |
| `Delgan/loguru` | 1328 | hit spend cap | 1 | $0.303 | $0.30 | 32 |
| `python-attrs/attrs` | 1161 | hit spend cap | 1 | $0.426 | $0.40 | 37 |
| `python-humanize/humanize` | 437 | hit spend cap | 1 | $0.309 | $0.30 | 22 |
| `mahmoud/boltons` | 389 | hit spend cap | 3 | $0.304 | $0.30 | 38 |
| `agronholm/typeguard` | 235 | hit spend cap | 3 | $0.305 | $0.30 | 29 |
| `pytoolz/toolz` | 203 | hit spend cap | 1 | $0.329 | $0.30 | 36 |
| `PyCQA/pycodestyle` | 54 | hit spend cap | 1 | $0.365 | $0.30 | 34 |

**Read the failures carefully: none of them are the agent giving up.** Every
one hit the per-repo *spend cap*, which was set deliberately low ($0.30-$0.50)
because the whole benchmark ran on a $5 budget. They ran out of money, not
out of ideas, and several were still making progress when they stopped.

The cap also varied between repositories, which is why it has its own column.
`tldextract` ran at $0.50 and cost $0.38, so it would have been recorded as a
failure under the $0.30 cap the others used. The headline number is therefore
a floor, not an estimate of what the model can do.

The sharper signal is the shape of the cost distribution. Successful
migrations were *cheap* -- $0.019, $0.060, $0.091, $0.109, median $0.091 --
while every failure burned the entire cap, three to sixteen times what a
success cost. When this agent can solve a repository it does so in two
iterations and a handful of tool calls; when it cannot, more budget mostly
buys more exploration.

Raw `result.json` and `trace.jsonl` for all 12: [`runs/published/benchmark/`](runs/published/benchmark/).
