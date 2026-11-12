# 归档契约两态测试最小修订方案（送审）

> 触发：`tests/test_deploy_quickmode.py::test_archive_default_unlimited` 断言
> `_archive/v001` 文件数 == 3，实测为 4。
> **归因更正**：`c6d0729`「sidecar 纳入交付包（P6）」是**本域此前已批准的工作**，
> 我上一轮把它称为「他线」是错的。本次失败的正确性质是：**归档契约随 sidecar 交付扩展，
> 而测试断言停留在扩展前**——属本域测试未随契约同步，**不是外部归因问题**。

## 一、契约事实（读 `deliver.py` 实测）

```python
def _archive_previous(out_dir, keep_versions):
    # keep_versions: None/缺省=不限份数；0=关闭归档；N>0=保留最近 N 份
    files = sorted(p for p in out_dir.iterdir() if p.is_file())   # ← 整目录文件
    arch_root = out_dir / "_archive"
    seq → dest = arch_root / f"v{seq:03d}"                        # vNNN 序号（非时间戳）
    for path in files: path.replace(dest / path.name)
```

sidecar 交付（`c6d0729`，P6）：`deliver()` 写 `{sid}_trade.py`（product）、报告、runbook，
**并写 `{sid}_trade.py.gen.json`（闸门认证件）**。因此 `out_dir` 在第二次 deploy 时含 **4 个文件**，
`_archive_previous` 整目录归档 ⇒ `_archive/v001` = 4 件。

⇒ **契约本身无缺陷**；缺陷在**测试只数文件总数**，既无法区分「哪些文件该归档」，
也无法在有/无认证件两态下各自成立。

## 二、测试最小修订（两态 × 五核）

### 态 A：**有**认证 sidecar（`gen.json` 存在）

| 核 | 断言 |
|---|---|
| ①名称 | `_archive/v001` 内文件名集合 == `{sid}_trade.py`、`{sid}_report.*`、`{sid}_runbook.*`、`{sid}_trade.py.gen.json`（**逐名列出，不数总数**） |
| ②旧版内容 | 归档内 `{sid}_trade.py` 的 **sha256 == 第一次 deploy 产物的 sha256**（证明归档的是上一版，不是新版拷贝） |
| ③归档位置 | 四件均位于 `<out>/_archive/v001/`；`out_dir` 根下此时只应有**本次**产物 |
| ④保留份数 | `keep_versions=None` 不限 ⇒ 三次 deploy 后 `_archive/v001..v002` 均在；`keep_versions=1` ⇒ 只余 `v002` |
| ⑤两次 deploy 成功 | 两次 `run_deploy` 的 `status` 均非 FAIL（**显式断言**，不以文件数代替成功性） |

### 态 B：**无**认证 sidecar（`gen.json` 不产生，如 `persist/gate` 关闭或认证降级）

| 核 | 断言 |
|---|---|
| ①名称 | `_archive/v001` 内文件名集合 == 三件（**不得**出现 `.gen.json`） |
| ②③④⑤ | 同态 A 逐项，但集合为三件 |

⇒ **两态必须分别成立**；禁止用「文件数 == 3 或 4」的宽容断言替代（那会让「该有却没有」
和「不该有却有了」都通过）。

## 三、实施边界

- **只改测试**，不改 `deliver.py` 的归档语义（契约无缺陷，改契约属另一种变更）。
- 现有 `test_archive_default_unlimited` 保留其「默认不限份数」语义，断言从「数总数」
  升级为「按名断言 ＋ 内容 sha ＋ 位置 ＋ 份数 ＋ 两次成功」。
- 新增态 B 用例（若当前测试夹具无法关闭 sidecar，需在夹具层加开关——**属测试内改动**，
  不改生产代码）。

## 四、为何不「机械把 3 改成 4」

「3→4」只能让当前红灯变绿，但：
① 无法证明归档的**是旧版内容**（②核缺失 ⇒ 「归档了新版拷贝」也能通过）；
② 无法覆盖**无认证件**的产品形态（态 B 缺失 ⇒ 该场景永远无测试）；
③ 保留份数语义（④）与两次成功（⑤）仍未被验证。
⇒ 违反「不要只断言文件总数」的直接要求。

## 五、状态

**本方案待送审**（②审）。审过前**不改码**；`224 passed / 1 skipped / 1 failed`
**不满足最终 ④**，S1 保持**待验**（撤回「新增失败 0 即可」的默认口径）。
