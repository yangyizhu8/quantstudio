#coding:gbk
# ============================================================================
# QS_INSTR_SNAPSHOT_EXPORT（D-30，2026-10-13，QMT 侧粘贴运行型——与 D-31 探针
# qmt_realtime_sector_probe-20261013.py 同款形态：粘贴进迅投知识库「新建策略」，
# 回测模式任意区间运行一次；无仓库依赖、无下单、纯只读+写一个 JSON 文件）。
#
# 目的：把 QMT 本地合约信息表冻结为 PIT 冻结合约快照（instrument snapshot），
#       供回测产物 D-30 快照层消费（设计合同：
#       docs/qmt-instrument-snapshot-design.md v0.3 §三）。
# 文档依据：docs/qmt/inner-api/07-行情函数.md:2446-2475（get_instrument_detail，
#       M5-8 实测签名=单位置参数）；:3288-3313（get_stock_list_in_sector，
#       D-12/D-31 定谳=单参调用，realtime 实参任何值回测模式均返回空列表）。
#
# 产物约定（与 source_import_qmt.py _QS_QMT_MODULE_HEADER 快照层逐字对齐）：
#   路径   D:\miniQMT策略实盘\QuantStudio\output\qmt_snapshot\instrument_snapshot.json
#   结构   {"enabled": true,
#           "meta":   {"exported_at": <ISO8601>, "n": <records 数>},
#           "records": {"<code>": <get_instrument_detail raw dict, 原样>}}
#   编码   UTF-8 / ensure_ascii=False / int 保持 int（禁 str 化）/ 码 sorted。
#   体积   ~5MB（产物级）：快照文件不进 git。
#
# 判读（输出行 QS_INSTR_SNAPSHOT_EXPORT 前缀，整段回贴给会话）：
#   n=      实际入册码数（= sector 名单数 - 逐码失败数）；
#   sha=    产物字节 sha256 前 16 位——与回测端首印审计行
#           QS_QMT_SNAPSHOT mode=on file=... sha=<同值> ... 逐位对照（指纹一致
#           才证明回测消费的就是本次导出的冻结版）；刷新=重跑本脚本+重跑回测
#           （快照层三态缓存：运行中变更文件不生效）。
#   逐码 get_instrument_detail 抛错/非 dict 返回 -> 该码不入册并打印
#           code_err=/code_bad_type= 行（运行期该码自动回落 live，不静默缺失）。
#
# 【实盘风险（红字）】回测 wrapper 无语境判别能力——快照文件存在即被任何语境
#   消费（含同机 QMT 实盘运行，合约表被冻结在导出时点）：实盘运行机不部署本
#   快照文件；删除文件即秒级禁用。本脚本本身只在粘贴运行时写一次文件。
# ============================================================================

import json
import os
import hashlib
from datetime import datetime

_SNAP_PATH = r'D:\miniQMT策略实盘\QuantStudio\output\qmt_snapshot\instrument_snapshot.json'
_SNAP_DIR = r'D:\miniQMT策略实盘\QuantStudio\output\qmt_snapshot'


def init(C):
    pass


def handlebar(C):
    if getattr(C, '_qs_snap_export_done', False):
        return
    try:
        C._qs_snap_export_done = True
    except Exception:
        pass
    codes = []
    try:
        # D-12/D-31 定谳：单参调用——严禁传 realtime（回测模式静默返回空列表）。
        codes = sorted(set(C.get_stock_list_in_sector('沪深A股')))
    except Exception as e:
        print('QS_INSTR_SNAPSHOT_EXPORT sector_err=%s' % repr(e)[:160])
        return
    print('QS_INSTR_SNAPSHOT_EXPORT sector_n=%d begin ts=%s'
          % (len(codes), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    records = {}
    err_n = 0
    for code in codes:
        try:
            raw = C.get_instrument_detail(code)  # M5-8 实测签名：单位置参数
        except Exception as e:
            err_n += 1
            print('QS_INSTR_SNAPSHOT_EXPORT code_err=%s err=%s' % (code, repr(e)[:80]))
            continue
        if not isinstance(raw, dict):
            # 非形态返回按缺数处理：不入册（运行期该码回落 live），如实登记。
            err_n += 1
            print('QS_INSTR_SNAPSHOT_EXPORT code_bad_type=%s type=%s'
                  % (code, type(raw).__name__))
            continue
        records[code] = raw  # raw dict 原样（int 保持 int，禁 str 化）
    doc = {
        'enabled': True,
        'meta': {'exported_at': datetime.now().isoformat(), 'n': len(records)},
        'records': records,  # codes 已 sorted（构建序即 sorted 稳定序）
    }
    try:
        if not os.path.isdir(_SNAP_DIR):
            os.makedirs(_SNAP_DIR)  # mkdir -p（约定路径不存在时建目录）
        data = json.dumps(doc, ensure_ascii=False).encode('utf-8')
        with open(_SNAP_PATH, 'wb') as f:
            f.write(data)
    except Exception as e:
        print('QS_INSTR_SNAPSHOT_EXPORT write_err=%s' % repr(e)[:160])
        return
    # sha 与产物字节同一对象：回测端 loader 对文件字节重新计算，两值须逐位一致。
    sha16 = hashlib.sha256(data).hexdigest()[:16]
    print('QS_INSTR_SNAPSHOT_EXPORT n=%d sector=%d err=%d file=%s sha=%s'
          % (len(records), len(codes), err_n, _SNAP_PATH, sha16))
    print('QS_INSTR_SNAPSHOT_EXPORT done ts=%s'
          % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
