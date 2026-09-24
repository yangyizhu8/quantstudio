# jabberwock 缺陷修复：ckey 空 shard → IndexError（mini 六步快审）

- 状态：**快审通过（2026-09-24）**——3 例亲跑绿 + 叠加回归 49 绿 + 纪律全执行；与 T3 笔（`bcd6268`）一并入下批推送
- 客户：**jabberwock**（客户机 `D:\hasym\PycharmProjects\QuantStudio`）
- 现象：`[qfq_orch] 周期异常停摆重锚环`
- 派单归因方向（已给）：缓存索引（ckey manifest）命中但 shard 文件全部缺失（被清理/过期/跨版本残留）
  → 修复 = 空列表降级为缓存 miss 走重取，**禁上抛**

## 0. 快审裁定留痕（守卫语义 = 直接降级，不采显式重试）

审核裁定理由（作为本设计依据保存）：
1. **重取路径已经重试过一次**——`miss_paths` 为空意味着**该窗口确无产出**；
2. **原地再重试大概率同样空**（循环风险）；
3. 降级 + WARNING 留痕、**周期继续**、**下周期自然重试**，与既有 **fail-soft 语义一致**；
4. **「不写 manifest 防 0-shard 条目污染」是关键一手**（否则后续命中判定会被 0-shard 条目长期污染）。

## 1. 缺陷链（取证，比派单描述更精确）

| 步 | 环节 | 现状 |
|---|---|---|
| ① | manifest 命中但 shard 文件全部缺失 | 逐文件 size 校验失败 → `all_ok=False` → **回退直连重取**（`mcp_adapter.py:1252`）——**该路径已有守卫**（`if all_ok and shard_paths:`） |
| ② | `_resolve_shard_paths()` 重取后**仍无产出** | `miss_paths = []`（`:1256`）——**无空值守卫** |
| ③ | `_read_ckey_cached(ckey, [])`（`:1261`） | 命中 `shard_paths[0]`（**`:1166`**）→ **`IndexError: list index out of range`** → 周期中断（重锚环停摆） |

**精确化**：派单归因的「命中路径」其实**已有守卫**；真凶在**重取路径**——重取（export 落盘）后仍无 shard 时
`miss_paths` 为空，直送 `_read_ckey_cached` 触发 IndexError。

## 2. 复现证据（先取证后落码）

`tests/test_mcp_ckey_empty_shards.py` 构造「manifest 在、shard 无、重取空」缓存态：

| 用例 | 修复前 | 修复后 |
|---|---|---|
| C1 `_read_ckey_cached(ckey, [])` | **IndexError**（`mcp_adapter.py:1166`） | PASS（空 DataFrame 降级） |
| C2 端到端 `_fetch_export_cached`（命中但 shard 缺 + 重取空） | **IndexError** | PASS（不抛异常、返回空、**不写 0-shard 条目**） |
| C3 正常命中路径（shard 在、size 匹配） | PASS | PASS（行为不变） |

修复前实测输出：`quantstudio\pipeline\sources\mcp_adapter.py:1166: IndexError`（2 failed, 1 passed）

## 3. 修复（两处最小改动，+18 行，单文件）

| # | 位置 | 改动 |
|---|---|---|
| ① | `_read_ckey_cached`（`:1166` 前） | **空 `shard_paths` → 降级返回空 DataFrame + WARNING**（视为缓存 miss，禁上抛） |
| ② | 重取路径（`:1256` 后） | **`miss_paths` 为空 → WARNING + `continue`**（本批降级为空、**周期继续**），且**不写 manifest**（防 0-shard 条目污染后续命中判定） |

**影响面**：正常路径（命中/正常重取）行为**不变**（C3 为证）；仅在「重取无产出」的异常态改变行为——由**中断**改为**降级继续**。

## 4. 验收

| 判据 | 结果 |
|---|---|
| 复现用例 3 例 | **3 passed**（C1/C2 转绿、C3 不变） |
| 叠加回归（MCP 相关 + T3） | **49 passed**（`test_mcp_wide_text_routing` / `fetch_routing` / `export_cache` / `streaming` / `t3_revision_detect`） |
| 编译 | `py_compile OK` |
| 纪律（与 T3 同文件，叠加执行） | edit 后即时 `git diff` 自检 ✓；精确清单提交 ✓；实施前零副作用回退点 ✓ |

## 5. 回退

- **禁用整文件 `git checkout <sha> -- mcp_adapter.py`**（CASE-009 教训：曾连带回退同文件 T4 改动）；
- 回退用**精确 hunk 反向补丁**或**新建反向提交**（本批仅 2 个 hunk，可精确定位）。

## 6. 客户侧答复口径（若 jabberwock 再问；审核给定）

> 此缺陷**不损数据**（属读取防御缺失）；重启后周期重试**仍会撞**；**修复推送后 `git pull` 即解**。

## 7. 证据与关联

- 复现/验收用例：`tests/test_mcp_ckey_empty_shards.py`
- 同文件在途：错误一 T3（`bcd6268`，已过验收）→ 本批叠加在同一文件，按纪律精确清单分别提交
- 关联案：CASE-005/007/008/009；本线台账 `docs/handoff/customer-ops-ledger.md`
