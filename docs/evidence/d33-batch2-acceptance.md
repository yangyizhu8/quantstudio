# D-33 批 2 验收证据（件 C 台账回填 ＋ 件 D 回哺机器化）

- **日期**：2026-10-16（批 2，用户裁定：边界表三裁定＋件 D 开工照准）
- **基线**：HEAD `e1768b3`（实测时点 2026-10-16 15:2x）
- **批 2 允许写入清单（方案 v1.2 §八）**：`docs/evidence/qmt-pipeline-convergence.md`／
  `scripts/qmt_kb_preflight.py`（新）／`quantstudio/strategy_compiler/source_import_qmt.py`（GBK 钩子落点）／
  `quantstudio/strategy_compiler/portability_rules.py`（如落规则）／`tests/**`（新）

## 一、件 C · P1 收敛台账回填（已完成）

- `docs/evidence/qmt-pipeline-convergence.md` 新增 **batch3 行**：N=9（D-23/24/26/27/28/29/30/31/32；
  **D-25 编号空缺如实标注**）／**R=1**（D-28 轮 GBK `≪` 复发）／回归门禁（114 passed＋CONTRACT GATE PASS＋
  矩阵哈希同批追认＋端测四轮一致）／收敛评注（连续 3 轮 R=0 未起，**本批不计收敛达成**）。
- 回哺载体清单**扩第 6 项**：`knowledge/qmt/` QD 条目库（批 2 起由件 D 读取器消费）。
- 关联节补 D-33 方案与边界表指针。
- 附带发现：该台账文件此前**未被 git 跟踪**（`??`）——随批入库。

## 二、件 D · 回哺机器化（已完成）

### 2.1 落点定谳（实测）

| 项 | 实测结论 |
| --- | --- |
| 产物写入点 | **`orchestrator.py::_write_qmt_product`（:102+）＝写盘点单点**（CLI/PyQt 同内核 `orchestrate_source` 经此写盘，`:500` 调用）；**原已 fail-closed**（`code.encode("gbk")` 失败即 raise，不静默替换） |
| 第二写者 | `scripts/qmt_reconvert_six.py:72-75`（canonical 重转门，独立 encode＋`wb` 写） |
| 渲染层 | wrapper（`source_import_qmt.py`）只产 unicode str——**不在模板内落盘** |
| `_QMT_HASH_SCOPE` | 20 常量（`_QS_QMT_MODULE_VERSION`＋12 模板＋`FQ_MAP`/枚举三＋`FUND_FIELD_MAP`＋`ALIAS_ZONE`＋`MODULE_HEADER`）——**宿主侧函数改动不触哈希** |

→ **②审条件①的「矩阵哈希同批追认」条款本轮不触发**（实测 `check_fund_matrix --check` = **OK**）。

### 2.2 交付物

| 交付 | 路径 | 内容 |
| --- | --- | --- |
| 写前拦截强化 | `quantstudio/strategy_compiler/orchestrator.py` | `_write_qmt_product`：失败消息带库条目指针 `QD-2026-0001`（B1 **不可豁免**）；成功/失败各一行 `QS_KB_WRITEGATE` 审计；**转码语义与写盘结果零变化**（纯增益） |
| 预检读取器 | `scripts/qmt_kb_preflight.py`（新） | 读 `knowledge/qmt/*.md` frontmatter → 已知坑清单；判定 B1（产物）/B2·B3（源码 BLOCK）/B4（WARN 待证）/W1（快照三态）/W4（能力边界）/W5（覆盖面）；`QS_KB_ACK: <QD-id> <理由>` 豁免降级＋留痕；`--json`；退出码 0/1/2 |
| 测试 | `tests/test_qmt_kb_preflight.py`（新） | 10 用例（见下） |
| 边界表裁定入册 | `docs/qmt-kb-preflight-boundary.md` | B4 移入 WARN 表＋升级路径；§六 裁定记录；§七 实施状态 |

### 2.3 批 2 验收四步（用户裁定口径）

| 步 | 判据 | 结果 |
| --- | --- | --- |
| ① | **B1–B3 各构造应阻断样本** | ✅ `test_b1_non_gbk_product_blocks`／`test_b1_missing_header_and_bom_block`／`test_b2_empty_required_arg_blocks`／`test_b3_realtime_arg_blocks` |
| ② | **非恒红对照样本** | ✅ `test_control_samples_pass`（合规产物＋合规源码 → 无 BLOCK） |
| ③ | **豁免样本** | ✅ `test_ack_exemption_downgrades_block_to_warn`（B2 命中＋`QS_KB_ACK` → 降 WARN 且留痕） |
| ④ | **六策略零回归** | ✅ `run_contract_gate.py --strategies` → **CONTRACT GATE PASS**（契约套件＋矩阵门禁＋6 策略 api_portability 全过） |
| 附 | 套件全绿 | ✅ `pytest tests/test_qmt_kb_preflight.py -q` → **10 passed**（`docs/evidence/d33-itemd-preflight-tests.txt`） |
| 附 | 哈希未变 | ✅ `check_fund_matrix --check` → **OK** |

### 2.4 缺陷处置与披露（实施期）

| # | 项 | 处置 |
| --- | --- | --- |
| D1 | orchestrator 补丁**锚点失配**（该文件为 CRLF，脚本锚点用 LF）——断言拦下，文件未被改动 | 改 CRLF 感知后重落（脚本自带锚点唯一性断言＝防半写） |
| D2 | B4 判据初版未覆盖本地 `get_fundamentals(table='eps')` 形态 | 扩展为窗口级判定（两形态覆盖） |
| D3 | B4 判据被**注释中的字段名**抑制（窗口含注释） | 窗口**剔除注释行**（确定性优先），测试转绿 |
| 编码委派 | **本轮不委派 `zcode_code`**（显式声明） | 原因：①与边界表裁定语义强耦合；②`zcode_code` 本会话 2 次中断＋2 次限流失败史；③时效（用户令加速） |

## 三、批 2 判定

**批 2 验收通过**（四步全过＋套件 10 passed＋哈希未变＋六策略零回归）；**R=1 的「先修管线」动作闭环**
（写前拦截落地＝非补丁了事）。批 2 改动随批双推（未推送）。

**剩余**：批 3（件 E 端测配额手册／件 F N=6 收敛判据与终点仪式）。
