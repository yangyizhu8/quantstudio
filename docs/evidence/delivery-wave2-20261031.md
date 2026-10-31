# W2 收口读数（003-delivery-bundle-import · 内核只读校验）

- 抓拍时间：2026-10-31 04:40（Asia/Shanghai）
- git HEAD：`7a0c6a30bee19fcb346d53987ba329dc9ce3db3d`
- daemon 状态：**running**（PID 29152，`--mode forever`）——**本轮未停/未启**；W2 全程 L2 fixture
- 门禁前置：`--wave 0` / `--wave 1` 均已 PASS

## 1 测试读数

```
python -m pytest tests/test_delivery_pure_gain.py tests/test_delivery_verify.py \
  tests/test_delivery_stats.py tests/test_delivery_precheck.py \
  tests/test_delivery_fixtures.py tests/test_delivery_export.py -q
```
**测试: 109 passed / 0 failed / 20.10s**（W1 的 100 + W2 新增 9 项校验流程用例）

其中 `tests/test_delivery_verify.py` 独立 **36 passed / 9.07s**（manifest 单测 27 + W2 流程 9）。

## 2 SC-005 三种坏包零改动（DATA_ROOT 六元组指纹逐位一致）

方法：对每个坏包，先对客户端 `DATA_ROOT`（update 模式 fixture）取指纹（路径/大小/mtime_ns/sha256/条目数/整体 digest），
校验后再取一次并逐位比对。

| 坏包构造 | 失败类 | 退出码 | 用户可见话术 | DATA_ROOT 指纹 |
|---|---|---|---|---|
| ① `qfq_aux.db` 追加 8 字节（SHA/字节数不符） | `1_package` | 4 | 交付包不完整或已损坏，请重新获取 | **未变（digest 相同）** |
| ② 包内主库换成 `partial_or_mixed` 库并重算 manifest 的 bytes/sha | `2_program_version` | 6 | 程序版本与数据版本不匹配，请更新程序 | **未变** |
| ③ `released=true` + 包内写入 cutover，配置 generations 指向 DATA_ROOT 之外 | `3_config` | 5 | 配置不匹配，请检查（两路径对照） | **未变** |

**SC-005: 三种坏包校验后 DATA_ROOT 指纹逐位一致（zero_mutation=True × 3）；另：包目录文件清单在四次校验前后恒为 `['manifest.json','qfq_aux.db','qfq_aux_mcp_gen1.db','quantstudio.db']`（校验不在包内留任何边车）**

## 3 四类失败分派端到端（FR-011，含 v4 第四类）

| 场景 | `failure_kind` | 退出码 | 话术（节选） |
|---|---|---|---|
| 包损坏（字节数不符） | `1_package` | 4 | 交付包不完整或已损坏，请重新获取 |
| schema 与程序期望不符 | `2_program_version` | 6 | 程序版本与数据版本不匹配，请更新程序 |
| 路由路径落在 DATA_ROOT 之外 | `3_config` | 5 | 配置不匹配，请检查 |
| `manifest_schema_version=2` | `4_manifest_version` | 6 | 包由更新版本的程序生成，请先更新程序 |

> 第四类必须**不崩、不猜**：`parse_manifest` 在版本过高时直接返回、不返回任何已解析字段（单测 `test_version_too_new_is_manifest_version_kind` 断言 `read.doc is None`）。

## 4 世代路由断言（plan §6）实测

```
ROUTE = {"resolved_path": "<DATA_ROOT>/qfq_aux.db",
         "target_path":   "<DATA_ROOT>/qfq_aux.db",
         "reason": "legacy:released=false",
         "matched": true,
         "legacy_security_note": "客户配置 released=false，按 legacy 判定；包内 qfq_aux.db 必须校验通过；如需 gen1 请修复 config"}
```
- 用**包内主库**做 `duckdb_read` 回调（解析"导入后的世界"）；
- gen1 分支实测：`reason=gen1:b6_formal_20260807_v2`，解析到 `<DATA_ROOT>/../elsewhere/qfq_aux_nextgen.db` ⇒ 判 `3_config`（不在 DATA_ROOT 内，且文件名不在包内）。

## 5 逐表统计与 fail-fast 顺序

| 项 | 读数 |
|---|---|
| 逐表 | `stock_daily / trade_date / row_count=2 / OK`（表集 = map ∩ 实际存在 = 1） |
| 体积升序 fail-fast | 破坏**最大件**（主库）后 `files_checked == 2` ⇒ 两个小件先校验完再失败（FR-019 主序生效） |
| 校验耗时 | 0.328 s（fixture 2.6 MB） |
| estimate 标注 | 改写 manifest 声明 `estimate` 后：校验通过 + 报告含「该表行数为估算值（包内 method=estimate；本次复核实测 count=…）」（A3 强化②） |

## 6 SC-013 基线相对审计

**SC-013: 与基线一致（PASS，退出码 0）——保护路径 35 个，有变化 0，新增未跟踪 无**

（W2 只新增 `quantstudio/delivery/verify.py` 的实现与 `tests/fixtures/delivery/env.py`，均在受保护路径集合之外。）

## 7 生产库未被触碰

`data/*.db` 六文件（大小 + mtime）与 W0 开工快照逐项比对：**变化 0 / 6**。

## 8 委派申报

- **本轮未委派 `zcode_code`**：W2 曾计划把"退出码映射 + reasons 聚合"委派出去，但实际检查发现这两件在 W0 已随 `constants.EXIT_BY_FAILURE_KIND` / `_MESSAGES` 落地，剩余工作（四要素校验编排、路由断言）属语义件、按分工须自写。
- 委派纪律不因"无可切分机械件"而免除申报：**本轮零委派，理由如上**。W3 的 journal 相位台账 / RESTORE_README 文案属机械件，届时按分工委派。

## 9 波次门禁字段

测试: 109 passed / 0 failed（20.10s；verify 子集 36 passed）
SC-005: 三种坏包（损坏/schema/配置）校验后 DATA_ROOT 指纹逐位一致（digest 相同 ×3）
SC-013: 与基线一致（PASS，35 保护路径 0 变化）
