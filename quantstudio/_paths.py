"""集中解析数据目录与数据库文件路径，消除各模块散落硬编码 ``quantstudio.db`` 路径。

解析优先级（从高到低）：
1. 环境变量 ``QUANTSTUDIO_DATA_ROOT``（便于部署时重定向，无需改代码）
2. ``config/data_config.json`` 中 ``path`` 字段的父目录（配置中心）
   - 相对路径锚定到项目根（不依赖 cwd，daemon 跨目录运行也正确）
   - 绝对路径原样使用
3. 回退：项目根 / ``data``（默认客户布局：数据库解压到 <项目根>/data/）

用法::

    from quantstudio._paths import db_path, DATA_ROOT
    conn = duckdb.connect(str(db_path()))                 # quantstudio.db
    qfq = QFQMaintenance(db_path())                       # 同上的简写
    export_dir = DATA_ROOT / "kline_db"                 # 其它数据子目录
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# quantstudio/_paths.py -> 项目根
_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config" / "profiles" / "mcp_only" / "data_config.json"


def get_data_root() -> Path:
    """返回数据目录绝对路径，按上方优先级解析。"""
    env = os.environ.get("QUANTSTUDIO_DATA_ROOT")
    if env:
        return Path(env)
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        p = cfg.get("path")
        if p:
            # config path 指向数据库文件本身；data root 是其父目录。
            # 相对路径锚定到项目根（不依赖 cwd，daemon/回测跨目录运行都正确）；
            # 绝对路径原样使用。
            pp = Path(p)
            full = pp if pp.is_absolute() else (_ROOT / pp)
            return full.parent.resolve()
    except Exception:
        # 配置缺失/损坏时回退，保证可导入、可定位
        pass
    return _ROOT / "data"


# 模块加载即解析一次（配置在会话内不变）
DATA_ROOT = get_data_root()


def db_path(name: str = "quantstudio.db") -> Path:
    """返回数据目录下数据库文件的绝对路径（默认 quantstudio.db）。"""
    return DATA_ROOT / name


def quarantine_db_path() -> Path:
    """返回 quarantine.db 的绝对路径。"""
    return db_path("quarantine.db")
