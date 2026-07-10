# Stage 1 Phase 1 Silver Data Audit

- Gate: `passed_with_warnings`
- Records: `10057`
- Eligible for gold: `10027`
- Blocked: `30`
- Blocked fraction: `0.2983%`
- Manual review: `pending`

## Source Profile

| Source | Records | Eligible | Blocked | chars p50 | chars p99 | Tools | Function calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| `glint-fable-5-traces` | 100 | 98 | 2 | 9834 | 32864 | 0 | 0 |
| `nvidia-opencodeinstruct` | 1000 | 1000 | 0 | 1354 | 3534 | 0 | 0 |
| `openthoughts-agent-v1-sft` | 1000 | 997 | 3 | 11757 | 24689 | 0 | 0 |
| `salesforce-apigen-mt-5k` | 3000 | 2975 | 25 | 12833 | 21458 | 3000 | 2983 |
| `salesforce-xlam-function-calling-60k` | 4957 | 4957 | 0 | 216 | 550 | 4957 | 4957 |

## Flags

- `invalid_turn_order`: 23
- `long_sample`: 33
- `too_long`: 7

## Decision

Automated quality gate passed for the Stage 1 smoke dataset; blocked records must be excluded from gold.

Production use still requires a human reviewer to update the generated review queue.
