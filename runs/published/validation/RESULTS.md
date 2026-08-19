# Benchmark set validation

Produced by `patchpilot run --max-iterations 1`. This establishes a
Python 3.8 baseline and runs the suite once on 3.12 **without ever
calling the model** -- total cost $0.00.

It is not a measure of the agent. The agent has not run against these
repos yet. What this table shows is that each repo is *admissible*:
it builds, it has a real passing baseline on 3.8, and it genuinely
breaks on 3.12 so there is migration work to do.

| Repo | Ref | Baseline (3.8) | On 3.12 before any fix | Wall (s) |
|---|---|---:|---|---:|
| `Delgan/loguru` | `0.6.0` | 1328 passing | `exit=1 collected=1352 passed=798 failed=2 skipped=3` | 409 |
| `python-attrs/attrs` | `21.4.0.post1` | 1161 passing | `exit=1 collected=1180 passed=1159 failed=13 skipped=7` | 183 |
| `pallets/click` | `8.1.3` | 568 passing | `exit=2 collected=542 passed=0 failed=0 skipped=0` | 31 |
| `more-itertools/more-itertools` | `v8.13.0` | 534 passing | `exit=1 collected=536 passed=503 failed=1 skipped=0` | 29 |
| `python-humanize/humanize` | `4.2.3` | 437 passing | `exit=0 collected=460 passed=437 failed=0 skipped=23` | 94 |
| `mahmoud/boltons` | `21.0.0` | 389 passing | `exit=1 collected=0 passed=0 failed=0 skipped=0` | 28 |
| `pallets/itsdangerous` | `2.1.2` | 297 passing | `exit=2 collected=66 passed=0 failed=0 skipped=0` | 29 |
| `pyeve/cerberus` | `1.3.4` | 242 passing | `exit=1 collected=0 passed=0 failed=0 skipped=0` | 128 |
| `agronholm/typeguard` | `2.13.3` | 235 passing | `exit=1 collected=0 passed=0 failed=0 skipped=0` | 36 |
| `pytoolz/toolz` | `0.11.2` | 203 passing | `exit=1 collected=204 passed=201 failed=3 skipped=0` | 38 |
| `scrapy/w3lib` | `v1.22.0` | 144 passing | `exit=1 collected=146 passed=143 failed=3 skipped=0` | 69 |
| `PyCQA/pycodestyle` | `2.8.0` | 54 passing | `exit=1 collected=54 passed=49 failed=5 skipped=0` | 78 |
| `john-kurkowski/tldextract` | `3.3.0` | 47 passing | `exit=1 collected=53 passed=46 failed=7 skipped=0` | 109 |
| `jaraco/zipp` | `v3.8.0` | 35 passing | `exit=0 collected=35 passed=8 failed=0 skipped=0` | 42 |

**14 repos, 5,674 baseline tests.**
