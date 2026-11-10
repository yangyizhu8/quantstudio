# -*- coding: utf-8 -*-
# ============================================================================
# QS 探针策略 · 判定 PTrade 是否支持「模块级 if 块内同名 def 覆盖同文件内调用」
#
# 用途：PD-0019 期 1 F1 根因判定（P-a 贴装文件不一致 / P-b 平台代码变换）
# 安全：**只打印、不下单、不读不写任何文件、无任何交易动作**（全部为 log.info）
# 贴装：与普通 PTrade 策略相同（模拟盘新建策略 → 粘贴全部内容 → 运行周期任选）
# 读数：运行后在平台日志里找下面两个关键字——
#         QS_PROBE_A=          ← 「同文件内调用」形态
#         QS_PROBE_B=          ← 「跨文件/外视角调用」形态
#
# 判读：
#   A=OVERRIDE 且 B=OVERRIDE  ⇒ 平台完全支持同名覆盖 ⇒ **P-b 排除** ⇒ 回查贴装文件（P-a）
#   A=ORIG     且 B=OVERRIDE  ⇒ 平台对「同文件内调用」做静态绑定 ⇒ **P-b 坐实**
#   A/B 其他组合              ⇒ 记录原始读数，回报后另行分析
# ============================================================================


def _probe_target():
    """原版（顶层 def）。"""
    return 'ORIG'


# 与产物 ISO TAIL 完全同构的覆盖形态：模块级 if 块内重新 def 同名函数
_QS_PROBE_ON = True
if _QS_PROBE_ON:
    def _probe_target():
        """覆盖版（模块级 if 块内 def）。"""
        return 'OVERRIDE'


def _probe_caller_samefile():
    """模拟产物内 order_target_value 体内的调用形态（同文件、函数体内、全局名查找）。"""
    return _probe_target()


def initialize(context):
    """首启即打印——无需等调仓、无需任何交易动作。"""
    # A：同文件内调用（决定 F1 性质的关键读数）
    try:
        log.info('QS_PROBE_A=%s' % _probe_caller_samefile())
    except Exception as exc:
        log.info('QS_PROBE_A=ERROR:%s' % exc)

    # B：外视角（模块字典）调用——用于区分「覆盖没成立」与「调用被绑定」
    try:
        log.info('QS_PROBE_B=%s' % globals()['_probe_target']())
    except Exception as exc:
        log.info('QS_PROBE_B=ERROR:%s' % exc)

    # C：辅助读数——平台版本与运行期信息（便于比对，不含任何敏感数据）
    try:
        log.info('QS_PROBE_C=python_ok, probe_version=20261110-1')
    except Exception:
        pass


def handle_data(context, data):
    """空实现——探针不做任何交易。"""
    pass
