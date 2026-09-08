# 写前快照基线 · get_index_day_bar 实施前（2026-09-08）

- 零副作用回退点（git stash create -u + store）：09734b4592aa236c6668203864994664c8ccbf09
- git status --porcelain 行数：216（工作区含大量他人/历史未提交改动，本会话不触碰）
- 本会话将改动的 repo 侧文件（精确清单）：
  - quantstudio/backtest/providers/duckdb_data_access.py（新增专用查询方法）
  - quantstudio/backtest/providers/duckdb_provider.py（透传）
  - quantstudio/backtest/ptrade_api.py（注入 API，共享核心文件）
  - tests/test_get_index_day_bar.py（新增）
  - README.md / docs/strategy_toolbox.md / docs/prompt_engineering.md（文档同步）
- skill 侧（仓库外）：validate_agent_strategy.py、ptrade-api-signatures.json、component-catalog.json、SKILL.md、api-capability-matrix.md（SHA-256 记入证据文档）
- 方案文档：docs/get-index-day-bar-design.md（终审通过稿）
