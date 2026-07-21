# Security Code and Market Classification Rules

> Rules version: `1.0.0`  
> Runtime authority: `quantstudio/backtest/libs/security_code_rules.py`  
> Generated from module metadata; this document is never a runtime dependency.

## Supported suffix aliases

| Market | Accepted input | QMT output | PTrade output |
|---|---|---|---|
| Shanghai | `.SH` / `.SS` / `.XSHG` / bare | `.SH` | `.SS` |
| Shenzhen | `.SZ` / `.XSHE` / bare | `.SZ` | `.SZ` |
| Beijing | `.BJ` / `.XBJ` / `.XBSE` / bare | `.BJ` | `.BJ` |

## Beijing Stock Exchange boundary

- Current BSE equity range: `920`.
- Legacy compatibility uses the exact official old/new mapping, never a blanket `4`/`8` prefix rule.
- Official legacy mapping count: **248**.
- Legacy prefixes present in the official mapping: `430`, `830`, `831`, `832`, `833`, `834`, `835`, `836`, `837`, `838`, `839`, `870`, `871`, `872`, `873`.
- `400xxx`, `420xxx`, and arbitrary unmapped `8xxxxx` codes are not BSE equities.
- Mapping source: `https://www.bse.cn/service/code_mapping.html`.

## Classification precedence

`index -> bse -> star_market -> chinext -> convertible_bond -> etf -> main_board -> unknown`

## Compatibility policy

- Suffix aliases are normalized, but historical security numbers are not rewritten to `920`; this preserves historical-data lookup semantics.
- Unknown bare codes retain the pre-PR1 Shanghai fallback.
- ETF, index, and convertible-bond checks precede main-board checks to prevent overlapping-range misclassification.
