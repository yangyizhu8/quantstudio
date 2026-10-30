# -*- coding: utf-8 -*-
"""P2 配置校验段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.3 / §6.2）。

职责：deploy.yaml 的加载与手工 schema 校验（dataclass + 手写校验，不引入 pydantic）；
档位合法性交叉校验（与 P1 画像的频率档位交叉）。

依赖说明：PyYAML>=6.0 为 pyproject.toml 既有 dependencies（已核实），
故直接采用 contract_table.yaml + deploy.yaml 的 YAML 形态，无需 JSON 降级路径。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

#: 执行时点档位（设计文档 §6.2——唯一语义决策点；日线策略强制二选一，禁止「不选」）
PROFILES = ("daily_eod_1450", "daily_open_0931", "minute_keep")

#: M3 支持的落盘关键点（on_order_change 需订单回调注入，属后续段；出现即校验失败并明示）
ALLOWED_SAVE_POINTS = frozenset({"after_trading_end"})

#: 状态文件默认**文件名**模板（PD-0015）。
#:
#: 只给文件名、不给目录：研究根目录由产物在运行时用 `get_research_path()` 取。
#: 依据 PTrade 官方文档（knowledge/platform/ptrade/01-API文档主页/001-page.md:2573）：
#: 「在部分券商里，会限制使用路径 /home/fly/，如果代码里面存在该路径字符，会提示无法保存，
#:  需要替换成 get_research_path()，效果一样」——故产物文本中**绝对不可出现该路径字面量**。
DEFAULT_STATE_FILENAME = "{strategy_id}_state.pkl"

#: 兼容别名（旧名保留一版；新代码请用 DEFAULT_STATE_FILENAME）
DEFAULT_STATE_FILE = DEFAULT_STATE_FILENAME

_STRATEGY_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class ConfigError(Exception):
    """配置校验失败（errors 为逐字段错误清单，结构化可机读）。"""

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------- 数据类 --

@dataclass
class ExecutionConfig:
    profile: str = ""


@dataclass
class PersistenceConfig:
    enabled: bool = True
    state_file: str = ""
    save_points: Tuple[str, ...] = ("after_trading_end",)


@dataclass
class GuardsConfig:
    limit_price_explicit: bool = True
    limit_up_down_block: bool = True
    suspension_block: bool = True
    order_idempotency: bool = True
    fundamentals_retry: int = 3


@dataclass
class NotifyConfig:
    email: str = ""


@dataclass
class ReportConfig:
    fidelity_notes: Tuple[str, ...] = ()


@dataclass
class OutputConfig:
    """交付输出目录配置（PD-0008 D3/D4）。

    - ``root``：输出根；空 = 缺省「仓库根 / Demo_account_Real_account」；相对路径按
      仓库根解析（保证 cwd 无关的确定性）；
    - ``dir_name``：策略子目录名（可中文）；空 = 用 ``strategy_id``；
    - ``keep_versions``：``None``/缺省 = 归档**不限份数**；``0`` = **关闭归档**
      （直接覆盖）；``N>0`` = 仅保留最近 N 份归档。
    """

    root: str = ""
    dir_name: str = ""
    keep_versions: Optional[int] = None


@dataclass
class DeployConfig:
    strategy_id: str = ""
    source: str = ""
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    guards: GuardsConfig = field(default_factory=GuardsConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


# ---------------------------------------------------------------- 校验辅助 --

def _require_dict(raw: object, key: str, errors: List[str]) -> Dict[str, object]:
    value = raw.get(key, {}) if isinstance(raw, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{key}: 应为映射（dict），实际为 {type(value).__name__}")
        return {}
    return value


def _require_bool(section: Dict[str, object], key: str, default: bool,
                  errors: List[str], path: str) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        errors.append(f"{path}.{key}: 应为布尔，实际为 {value!r}")
        return default
    return value


# ---------------------------------------------------------------- 加载与校验 --

def load_config(raw: object) -> DeployConfig:
    """从 yaml.safe_load 的原始 dict 构建 DeployConfig；校验失败抛 ConfigError。"""
    errors: List[str] = []
    if not isinstance(raw, dict):
        raise ConfigError([f"配置根节点应为映射（dict），实际为 {type(raw).__name__}"])

    strategy_id = raw.get("strategy_id", "")
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        errors.append("strategy_id: 必填且非空字符串")
    elif not _STRATEGY_ID_RE.match(strategy_id):
        errors.append(f"strategy_id: 仅允许 [A-Za-z0-9_-]，实际为 {strategy_id!r}")

    source = raw.get("source", "")
    if not isinstance(source, str) or not source.strip():
        errors.append("source: 必填且非空字符串（回测策略 .py 路径）")

    execution_raw = _require_dict(raw, "execution", errors)
    profile = execution_raw.get("profile", "")
    if profile not in PROFILES:
        errors.append(f"execution.profile: 必须为 {' | '.join(PROFILES)} 之一，实际为 {profile!r}")

    persistence_raw = _require_dict(raw, "persistence", errors)
    p_enabled = _require_bool(persistence_raw, "enabled", True, errors, "persistence")
    state_file = persistence_raw.get("state_file", "")
    if state_file is not None and not isinstance(state_file, str):
        errors.append(f"persistence.state_file: 应为字符串，实际为 {state_file!r}")
        state_file = ""
    # PD-0015：state_file 必须是**纯文件名**（不得含目录/盘符）
    # 依据：部分券商禁止代码中出现 /home/fly 等绝对路径（官方 001-page.md:2573），
    # 研究根目录由产物运行时 get_research_path() 获取；此处拒绝而非静默改写。
    if isinstance(state_file, str) and state_file.strip():
        _sf = state_file.strip()
        if ("/" in _sf) or ("\\" in _sf) or re.match(r"^[A-Za-z]:", _sf) or _sf.startswith("~"):
            errors.append(
                f"persistence.state_file: 只接受纯文件名，实际为 {state_file!r}"
                "（平台禁用绝对路径——部分券商限制研究目录字面量，代码含该路径即无法保存；"
                "根目录由 get_research_path() 运行时获取，请改填形如 "
                f"'{(state_file.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]) or 'xxx_state.pkl'}')")
    save_points_raw = persistence_raw.get("save_points", ["after_trading_end"])
    if not isinstance(save_points_raw, list):
        errors.append(f"persistence.save_points: 应为列表，实际为 {save_points_raw!r}")
        save_points_raw = ["after_trading_end"]
    save_points: List[str] = []
    for sp in save_points_raw:
        if not isinstance(sp, str):
            errors.append(f"persistence.save_points: 元素应为字符串，实际为 {sp!r}")
        elif sp not in ALLOWED_SAVE_POINTS:
            errors.append(
                f"persistence.save_points: 不支持 {sp!r}（M3 仅支持 "
                f"{sorted(ALLOWED_SAVE_POINTS)}；on_order_change 需订单回调注入，属后续段）")
        elif sp not in save_points:
            save_points.append(sp)

    guards_raw = _require_dict(raw, "guards", errors)
    g_limit_price = _require_bool(guards_raw, "limit_price_explicit", True, errors, "guards")
    g_limit_ud = _require_bool(guards_raw, "limit_up_down_block", True, errors, "guards")
    g_susp = _require_bool(guards_raw, "suspension_block", True, errors, "guards")
    g_idem = _require_bool(guards_raw, "order_idempotency", True, errors, "guards")
    g_retry = guards_raw.get("fundamentals_retry", 3)
    if isinstance(g_retry, bool) or not isinstance(g_retry, int) or g_retry < 0:
        errors.append(f"guards.fundamentals_retry: 应为非负整数，实际为 {g_retry!r}")
        g_retry = 3

    notify_raw = _require_dict(raw, "notify", errors)
    email = notify_raw.get("email", "")
    if not isinstance(email, str):
        errors.append(f"notify.email: 应为字符串（留空禁用），实际为 {email!r}")
        email = ""

    report_raw = _require_dict(raw, "report", errors)
    notes_raw = report_raw.get("fidelity_notes", [])
    if not isinstance(notes_raw, list):
        errors.append(f"report.fidelity_notes: 应为列表，实际为 {notes_raw!r}")
        notes_raw = []
    fidelity_notes: List[str] = []
    for note in notes_raw:
        if not isinstance(note, str):
            errors.append(f"report.fidelity_notes: 元素应为字符串，实际为 {note!r}")
        else:
            fidelity_notes.append(note)

    # PD-0008：输出目录配置（D3 契约变更 + D4 重转语义）
    output_raw = _require_dict(raw, "output", errors)
    out_root = output_raw.get("root", "")
    if not isinstance(out_root, str):
        errors.append(f"output.root: 应为字符串，实际为 {out_root!r}")
        out_root = ""
    out_dir_name = output_raw.get("dir_name", "")
    if not isinstance(out_dir_name, str):
        errors.append(f"output.dir_name: 应为字符串，实际为 {out_dir_name!r}")
        out_dir_name = ""
    keep_raw = output_raw.get("keep_versions", None)
    keep_versions: Optional[int] = None
    if keep_raw is not None:
        if isinstance(keep_raw, bool) or not isinstance(keep_raw, int) or keep_raw < 0:
            errors.append(
                f"output.keep_versions: 应为非负整数或 null（null/缺省=不限份数、"
                f"0=关闭归档、N>0=保留最近 N 份），实际为 {keep_raw!r}")
        else:
            keep_versions = keep_raw

    if errors:
        raise ConfigError(errors)

    # 状态文件缺省按 strategy_id 填充（设计文档 §5.3 示例形态）
    if p_enabled and not state_file:
        state_file = DEFAULT_STATE_FILENAME.format(strategy_id=strategy_id)

    return DeployConfig(
        strategy_id=strategy_id,
        source=str(source),
        execution=ExecutionConfig(profile=str(profile)),
        persistence=PersistenceConfig(
            enabled=p_enabled,
            state_file=state_file,
            save_points=tuple(save_points),
        ),
        guards=GuardsConfig(
            limit_price_explicit=g_limit_price,
            limit_up_down_block=g_limit_ud,
            suspension_block=g_susp,
            order_idempotency=g_idem,
            fundamentals_retry=int(g_retry),
        ),
        notify=NotifyConfig(email=email),
        report=ReportConfig(fidelity_notes=tuple(fidelity_notes)),
        output=OutputConfig(root=out_root, dir_name=out_dir_name,
                            keep_versions=keep_versions),
    )


def load_config_from_yaml(text: str) -> DeployConfig:
    """从 YAML 文本加载（run_deploy 预载阶段使用）。"""
    return load_config(yaml.safe_load(text))


# ---------------------------------------------------------------- 交叉校验 --

def cross_validate(cfg: DeployConfig, profile: Dict[str, object]) -> List[str]:
    """P2 与 P1 画像的档位交叉校验（设计文档 §5.2 P2 / §6.2）。

    返回错误清单（空 = 通过）；频率 unknown 时交由 deploy.yaml 显式指定（警告入画像）。
    """
    errors: List[str] = []
    freq = profile.get("frequency", "unknown")
    if freq == "daily" and cfg.execution.profile == "minute_keep":
        errors.append(
            "档位交叉校验失败：日线策略禁止选择 minute_keep（日线回测盘后运行+收盘撮合直接映射"
            "到交易盘后会变隔夜单，F8；§6.2 强制在 daily_eod_1450 / daily_open_0931 中二选一）")
    elif freq == "minute" and cfg.execution.profile != "minute_keep":
        errors.append(
            f"档位交叉校验失败：分钟策略不得选日线执行档（{cfg.execution.profile}），"
            "应保持 minute_keep（§5.2 P2）")
    lifecycle = profile.get("lifecycle", [])
    mounts = profile.get("run_daily_mounts", [])
    hd_stub = bool(profile.get("handle_data_stub"))
    if cfg.execution.profile != "minute_keep" and "handle_data" in lifecycle and mounts:
        # PD-0007 混合形态细分：诊断型 stub（不参与决策）+ 恰 1 挂载 → 以挂载目标为
        # 部署执行体（D2′，stub 原样保留）；多挂载拒绝（D3′）；非 stub 拒绝并报判据
        # 命中（D4′——实质语句与挂载并存属双驱动风险）
        if not hd_stub:
            reasons = [str(r) for r in (profile.get("handle_data_stub_reasons") or [])]
            why = ("；判据命中: " + "，".join(reasons[:5])) if reasons else ""
            errors.append(
                "档位交叉校验失败：混合形态暂不支持——handle_data 含实质语句（非诊断型 "
                f"stub），与 run_daily 挂载（{mounts}）并存构成双驱动风险{why}；"
                "请用纯 handle_data 源或纯 run_daily 源（PD-0007 D4′）")
        elif len(mounts) != 1:
            errors.append(
                f"档位交叉校验失败：诊断型 stub + {len(mounts)} 个 run_daily 挂载——"
                f"多挂载源暂不支持（PD-0006 D3 单挂载限制），挂载清单: {mounts}")
    if cfg.execution.profile != "minute_keep" and "handle_data" not in lifecycle:
        # PD-0006 扩展：run_daily 源形态（恰 1 个执行挂载）作为第二合法输入
        if len(mounts) == 1 and mounts[0].get("func"):
            pass  # 合法：P3 将包装源挂载函数并重挂到档位时点（设计文档 §4/D2）
        elif len(mounts) > 1:
            errors.append(
                f"档位交叉校验失败：检测到 {len(mounts)} 个 run_daily 挂载——"
                "多挂载源暂不支持（PD-0006 D3 单挂载限制；错峰多策略形态待后续扩展），"
                f"挂载清单: {mounts}")
        else:
            errors.append(
                "档位交叉校验失败：日线执行档要求源码存在 handle_data（挂载包装的输入），"
                f"或恰 1 个 initialize 内 run_daily 挂载（PD-0006 源形态），"
                f"实际生命周期函数: {lifecycle or '无'}")
    # 交易环境 run_daily+run_interval 累计上限 5（平台文档；部署产物恒 1 个执行挂载，
    # 源挂载在 P3 被替换不残留——此校验为防御性预算断言）
    if cfg.execution.profile != "minute_keep" and len(mounts) > 4:
        errors.append(
            f"档位交叉校验失败：源 run_daily 挂载 {len(mounts)} 个，叠加部署挂载将超"
            "平台交易定时任务上限 5（run_daily+run_interval 累计，超限堵塞）")
    return errors
