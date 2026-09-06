# 任务一实施与验收证据：get_Ashares 北交排除 + PIT 退市过滤 + 残差复跑设计（2026-09-06）

- 流水线：Step 1 方案包（四批复①②③④）→ Step 3 实施 → **Step 4 验收**
- 回退点：`295714a` 系 + 本任务 stash store：`0ca8c0099e7bab7694b7540d7b1ea8c26a38c789`

## 1. 实施清单

| 文件 | 改动 |
|---|---|
| `libs/security_code_rules.py` | 新谓词 `is_bje_excluded`（blanket：920 前缀 + BSE_LEGACY_TO_920 + 4xx/8xx 全段；lru_cache）——`is_bse_market` **零改动**（BSE 合法消费方不受污染） |
| `ptrade_api.py` get_Ashares | ①**默认翻转**（exclude_bse None→默认 True 排除；False=显式回退逃生门）②谓词切换 is_bse_market→is_bje_excluded ③**PIT 退市过滤** `_filter_delisted_pit`（stock_delist 惰性一次性预载内存 set + 退市日 ≤T 剔除；date=None 跳过兼容；fail-open） |
| `tests/test_task1_ashares_bje.py`（新） | T1-T5b 契约测试 |

**注意（施工中自查修正）**：PIT 过滤初版实现存在热路径逐码 DB 连接缺陷（细则⑤即时自检发现）→ 重写为**惰性一次性预载内存 set**（热路径零 DB 往返）。

## 2. 验收结果

| 项 | 结果 |
|---|---|
| 谓词行为 8 例 | ✅ 全 PASS（920001/430047/830799/430139 排除；600000/000001/300750/688981 保留） |
| T4 差集锁定 | ✅ 800001/400001：bje=True / bse=False（blanket vs is_bse_market 差集行为实证） |
| T1 默认排除 | ✅ 混合池产 0 北交段、沪深全保留（.SS 平台格式断言修正后） |
| T2 显式回退 | ✅ exclude_bse=False 保留北交（逃生门） |
| T5/T5b PIT 过滤 | ✅ 退市码剔除 + date=None 兼容跳过 |
| stock_delist 实测 | ⏳ **BLOCKED(外部依赖：DB 长驻进程 PID 59640 等占用，10×45s 退避未释放)**——DESCRIBE/334 条覆盖确认待锁释放补跑 |

## 3. DB 锁阻塞申报（BLOCKED 项）

两口径计数 + 4xx/8xx 枚举 + stock_delist DESCRIBE 三项实测**因 DB 排他锁未执行**（多长驻 python 进程 17:49/9-3/8-22 起持续持有，10×45s 退避未释放；未杀进程按宽匹配教训）。**批复①条件⑵（4xx/8xx 码枚举）与批复③前置（DESCRIBE）留待锁释放补跑**——若实测推翻前提（4xx/8xx 存在实盘交易码等）按证据重报停改。

## 4. 回退

- 回退点 `0ca8c00` + `295714a` 链在案；
- 默认翻转回退 = exclude_bse 默认值单点还原；谓词回退 = security_code_rules 单函数删除。