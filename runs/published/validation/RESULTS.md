# Benchmark set validation

Produced by `patchpilot run --max-iterations 1`: establishes a Python 3.8
baseline and runs the suite once on 3.12 **without ever calling the model**.
Total cost $0.00.

This is not a measure of the agent -- it has not run against these repos yet.
It shows each repo is *admissible*: it builds, has a real passing baseline on
3.8, and genuinely breaks on 3.12 so there is migration work to do.

| Repo | Ref | Baseline (3.8) | On 3.12 before any fix | Wall (s) |
|---|---|---:|---|---:|
| `Delgan/loguru` | `0.6.0` | 1328 passing | `exit=1 collected=1352 passed=798 failed=2 skipped=3` | 347 |
| `python-attrs/attrs` | `21.4.0.post1` | 1161 passing | `exit=1 collected=1180 passed=1159 failed=13 skipped=8` | 268 |
| `pallets/click` | `8.1.3` | 568 passing | `exit=2 collected=542 passed=0 failed=0 skipped=0` | 70 |
| `python-humanize/humanize` | `4.2.3` | 437 passing | `exit=0 collected=460 passed=437 failed=0 skipped=23` | 60 |
| `mahmoud/boltons` | `21.0.0` | 389 passing | `exit=1 collected=0 passed=0 failed=0 skipped=0` | 50 |
| `pallets/itsdangerous` | `2.1.2` | 297 passing | `exit=2 collected=66 passed=0 failed=0 skipped=0` | 44 |
| `pyeve/cerberus` | `1.3.4` | 242 passing | `exit=1 collected=0 passed=0 failed=0 skipped=0` | 302 |
| `agronholm/typeguard` | `2.13.3` | 235 passing | `exit=1 collected=0 passed=0 failed=0 skipped=0` | 55 |
| `pytoolz/toolz` | `0.11.2` | 203 passing | `exit=1 collected=204 passed=201 failed=3 skipped=0` | 76 |
| `scrapy/w3lib` | `v1.22.0` | 144 passing | `exit=1 collected=146 passed=143 failed=3 skipped=0` | 41 |
| `PyCQA/pycodestyle` | `2.8.0` | 54 passing | `exit=1 collected=54 passed=49 failed=5 skipped=0` | 49 |
| `john-kurkowski/tldextract` | `3.3.0` | 47 passing | `exit=1 collected=53 passed=46 failed=7 skipped=0` | 77 |

**12 repos, 5,105 baseline tests.**
