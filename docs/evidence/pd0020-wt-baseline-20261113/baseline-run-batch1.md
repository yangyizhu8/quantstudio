# 基线 worktree 复现（第一批：test_source_import）

> 基线锚 `7826aedbf9d634aabb48c2f7c0d988f490c78b07`，detached，未提交差异 0。
> 命令：`python -m pytest tests/test_source_import.py -q`（cwd = worktree 根）
> 结果：**7 failed / 50 passed**，退出码 1。

## 一、取证结论

| nodeid | 基线(7826aed) | 当前(135 清单) | 分类 |
|---|---|---|---|
| `test_22_include_false_signature_a_unchanged` | **FAILED** | FAILED | **已证基线既有** |
| `test_23_include_false_count_first_unchanged` | **FAILED** | FAILED | **已证基线既有** |
| `test_27_shim_default_include_false` | **FAILED** | FAILED | **已证基线既有** |
| `test_32_include_mapping_signature_a_daily_explicit_unit` | **FAILED** | FAILED | **已证基线既有** |
| `test_33_include_mapping_count_first_positional_daily` | **FAILED** | FAILED | **已证基线既有** |
| `test_34_include_no_mapping_all_freqs` | **FAILED** | FAILED | **已证基线既有** |
| `test_35_include_true_no_mapping` | **FAILED** | FAILED | **已证基线既有** |
| `test_08_remove_banding` | **PASSED** | FAILED | **非基线既有（候选新增）** |
| `test_08b_rewrite_literal_inline` | **PASSED** | FAILED | **非基线既有（候选新增）** |

### 适用边界（严格按用户裁定）

**「基线同样失败」只对该版本（`7826aed`）、该配置、该输入成立**——
①**不免除当前修复义务**；②**不直接把失败归给本批**（`7826aed` → 当前 HEAD 之间夹多笔**他线**提交，
本批只是其中一段）；③test_08/08b「基线通过」同样**不等于本批引入**，须在
`7826aed..当前` 区间内进一步二分。

## 二、误判纠正（我方错误）

| 我的判断 | 实际 | 纠正 |
|---|---|---|
| `test_delivery_gui_route_parity.py:L20` 硬编码生产 `aux_db_path` ⇒ **排除** | L20 是 `REPORT` **字典测试数据**；`format_route_diag_text(REPORT)` **只格式化文本、不读库** | **误判，该项安全可跑** |
| `test_daemon_gui_start_paths.py` 有 `Popen` ⇒ **永久排除** | 该文件 docstring L14-17 明写：**全部临时 data_root（monkeypatch `_data_root`）**、A4 自建**占锁 dummy 子进程跑完即清理**、**不启停生产 daemon** | **误判，该项安全可跑**（且不在 135 清单） |
| 凭据「安全」 | **读取环境变量不等于没有真实凭据**；本机未设置不证明其他环境安全 | **改判为「未证实安全」** |

## 三、未运行声明

- `test_fin_growth_dividend_staging_tool`（10 项）：**尚未判定**是否触 L2497 `Popen` 路径，**未运行**
- 其余 126 项：**未运行**（本轮只跑 `test_source_import` 一个文件）

## 四、四类结论当前计数

| 类别 | 数量 |
|---|---|
| **已证基线既有** | **7**（test_22/23/27/32/33/34/35） |
| **非基线既有（候选新增，未定归属）** | **2**（test_08、test_08b） |
| **无法安全运行** | **0**（本轮纠正后无此项；test_fin_growth 待判定） |
| **契约变化** | **0** |
| **仍未运行** | **126** |
