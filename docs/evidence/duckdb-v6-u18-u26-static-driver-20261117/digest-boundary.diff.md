# r10 摘要值边界 —— 最小 diff 登记（对照 r9 驱动 26285 B / `5B8EC1B69040DA906B4EFB9E6E65DCE0AF3413265CE67F5A0B5465BC412F45C5`）

> 原则：**只在 SHA-256 值边界归一大小写**；其他字段仍 `-ceq` 严格比较；七段抽取 pin 与通知一字未动。

## 新增文件

`_digestcmp.ps1`（895 B，`158746396792143BEE1A5A2DCEA36C0489A49DADED8BEFEAB9A2C8E110392EFB`）
——摘要边界**唯一实现**，驱动与静态门 dot-source 同一份，避免「负控测的是复制品」。

```powershell
function Test-DigestHex([string]$v) {
    if ($null -eq $v) { return $false }
    return ($v -match '^[0-9A-Fa-f]{64}$')
}
function Same-Digest([string]$a, [string]$b) {
    if (-not (Test-DigestHex $a)) { throw ('DIGEST-FORMAT: 左值非 64 位 hex -> [' + [string]$a + ']') }
    if (-not (Test-DigestHex $b)) { throw ('DIGEST-FORMAT: 右值非 64 位 hex -> [' + [string]$b + ']') }
    return ($a.ToLowerInvariant() -eq $b.ToLowerInvariant())
}
```

## 驱动改动（9 处，逐条）

| # | 位置 | 原 | 新 |
|---|---|---|---|
| 1 | stamp | `$stamp = Get-Date -Format 'yyyyMMddTHHmmss'` | `$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss') + 'Z'` + `$stampLocal` |
| 2 | pin 区 | — | `$PIN_DIGEST = '15874639…2EFB'` |
| 3 | Add-Case | 记 `Pass=$pass` | 增 `Cmp='EXACT'`（其余不变） |
| 4 | 新增 | — | `Add-CaseDigest`：`$pass = Same-Digest $expect $got`，记 `Cmp='DIGEST-NORM'`，**原显示值原样入表** |
| 5 | 顶层 | — | `$script:neg = New-Object System.Collections.ArrayList` |
| 6 | cases.tsv | `id/group/arg/expect/got/pass` | 增 `cmp` 列：`id/group/arg/expect/got/cmp/pass` |
| 7 | PARSE 后 | — | 校验 `_digestcmp.ps1` pin（失败 `DIGEST-PIN:`）→ 复制进 evDir → dot-source |
| 8 | 5 处断言 | `Add-Case …` | `Add-CaseDigest …`（FILE-EQ-META／PFX-EQ／PS-RECOMPUTE-EQ／ENC-UTF8-OK-BYTES／ENC-VALUE-PRESERVED） |
| 9 | `ENC-ENSUREASCII-FILE-DIFF` | `$eaFile -ne $utf8Hex` | `Same-Digest $eaFile $utf8Hex` 取反（判差也走同一边界） |

## 负控（3 条，纯内存，**不计入业务 20 分母**）

| ID | 构造 | 期望 |
|---|---|---|
| `NC1-DIGEST-CASE-EQ` | 同值全大写 vs 全小写 | `True` |
| `NC2-DIGEST-ONECHAR-NE` | 末位 `0`→`1`（一位真实变化） | `False` |
| `NC3-DIGEST-BADHEX-REJECT` | 63 位 / 含非 hex 字符 | 两者均 `THROW` |

## 明确未改（防"顺手改松"）

- `Add-Case` 仍是 `-ceq`；路径、版本串、字段串、布尔等**全部严格**；
- 六段抽取 pin 与 synth pin 未动；通知未动（仍 117882 B / `598741EF…59451`）；
- 通知内 8a 的比较是 PS `-ne`（本身大小写不敏感）——**属通知自身语义，不在我改动范围**。
