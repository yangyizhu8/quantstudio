#!/usr/bin/env python3
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

NEWLINE = "\n"
KB_INFO = "knowledge/kb-info.yaml"
KEEP = ["scripts/kb_hook.py", "tests/test_kb_parse_status.py"]
REQUIRED_KEYS = ["total", "checked", "bad", "skipped", "families_used"]
REQUIRED_BEFORE = ["inputs.list", "inputs.before.txt", "inputs.before.sha256",
                   "baseline.json", "baseline.json.rc", "errset.before.json.rc",
                   "phase.before.done"]

RUN_DIR = ""


def die(msg):
    print("STOP: " + msg)
    sys.exit(9)


def path(name):
    return os.path.join(RUN_DIR, name)


def sha(path_str):
    with open(path_str, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write_text(name, text):
    with io.open(path(name), "w", encoding="utf-8", newline=NEWLINE) as fh:
        fh.write(text)


def read_text(name):
    with io.open(path(name), encoding="utf-8") as fh:
        return fh.read()


def load_inputs():
    items = [ln.strip() for ln in read_text("inputs.list").split("\n")]
    files = [i for i in items if i]
    if not files:
        die("inputs.list 为空")
    return files


def git_ls_files(paths, must_contain=None):
    """Git 失败门：rc!=0 即停；关键路径必须出现在 index 文本中。"""
    argv = ["git", "-c", "core.quotepath=false", "ls-files", "-s", "--"] + paths
    res = subprocess.run(argv, capture_output=True)
    if res.returncode != 0:
        die("git ls-files 失败 rc=" + str(res.returncode))
    text = res.stdout.decode("utf-8", "replace")
    if must_contain is not None and must_contain not in text:
        die("index 缺条目: " + must_contain)
    return text


def load_payload(name):
    """JSON 门：文件必须存在、非空、可解析且含必需键——防"崩溃空输出"混入对照。"""
    target = path(name)
    if not os.path.isfile(target):
        die("缺 " + name)
    with open(target, "rb") as fh:
        raw = fh.read()
    if not raw.strip():
        die(name + " 为空（疑似 kb_hook 崩溃/无输出）")
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        die(name + " 不可解析（" + type(exc).__name__ + ":" + str(exc) + "）")
    missing = [k for k in REQUIRED_KEYS if k not in doc]
    if missing:
        die(name + " 缺必需键: " + str(missing))
    return doc


def kb_hook(files, repo_root=".", kb_info_rel=KB_INFO):
    argv = [sys.executable, "scripts/kb_hook.py",
            "--repo-root", repo_root,
            "--kb-info", kb_info_rel,
            "--strict", "--json",
            "--files"] + files
    res = subprocess.run(argv, capture_output=True)
    if res.stdout is None:
        die("kb_hook 无输出")
    return res.returncode, res.stdout


def freeze(tag):
    files = load_inputs()
    write_text("inputs." + tag + ".txt", NEWLINE.join(files) + NEWLINE)
    lines = []
    for rel in files:
        if not os.path.isfile(rel):
            die("输入文件缺失: " + rel)
        lines.append(sha(rel) + "  " + rel)
    write_text("inputs." + tag + ".sha256", NEWLINE.join(lines) + NEWLINE)
    write_text("kb-info." + tag + ".sha256", sha(KB_INFO) + NEWLINE)
    print("freeze(" + tag + "): " + str(len(files)) + " 件")


def capture(tag, files, repo_root="."):
    rc, out = kb_hook(files, repo_root=repo_root)
    with open(path(tag + ".json"), "wb") as fh:
        fh.write(out)
    write_text(tag + ".json.rc", str(rc) + NEWLINE)
    print(tag + ": rc=" + str(rc) + " bytes=" + str(len(out)))
    return rc


def sandbox():
    marker = path("errset.sandbox.path")
    if os.path.isfile(marker):
        return read_text("errset.sandbox.path").strip()
    sand = tempfile.mkdtemp(prefix="kb_err_")
    os.makedirs(os.path.join(sand, "knowledge", "ops"), exist_ok=True)
    shutil.copyfile(KB_INFO, os.path.join(sand, "knowledge", "kb-info.yaml"))
    body = NEWLINE.join(["---", "id: OPS-2026-9004", 'trigger: ""', "---", ""])
    target = os.path.join(sand, "knowledge", "ops", "OPS-2026-9004.md")
    with io.open(target, "w", encoding="utf-8", newline=NEWLINE) as fh:
        fh.write(body)
    write_text("errset.sandbox.path", sand + NEWLINE)
    return sand


def errset(tag):
    sand = sandbox()
    found = glob.glob(os.path.join(sand, "knowledge", "ops", "OPS-*.md"))
    rel = []
    for item in found:
        rel.append(os.path.relpath(item, sand).replace(os.sep, "/"))
    if not rel:
        die("errset 沙箱内无 OPS 文件")
    rc, out = kb_hook(rel, repo_root=sand, kb_info_rel=KB_INFO)
    with open(path("errset." + tag + ".json"), "wb") as fh:
        fh.write(out)
    write_text("errset." + tag + ".json.rc", str(rc) + NEWLINE)
    print("errset." + tag + ": rc=" + str(rc))
    return rc


def assert_gates():
    import yaml
    with io.open(KB_INFO, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh.read())
    got = {}
    for fam in doc["families"]:
        if "placeholder_gate" in fam:
            got[fam["id"]] = (fam.get("placeholder_gate"),
                              fam.get("placeholder_gate_since"))
    print("gates = " + str(got))
    expected = {"qd": "off", "pd": "off", "db": "off", "ops": "warn"}
    for key, val in expected.items():
        pair = got.get(key, (None, None))
        if pair[0] != val:
            die("档位不符: " + key)
    ops = got.get("ops", (None, None))
    if ops[1] != "2026-11-13T20:56:02+08:00":
        die("ops since 不符")


def pin():
    src = "scripts/kb_hook.py"
    dst = path("rollback/scripts__kb_hook.py.pre.bin")
    shutil.copyfile(src, dst)
    digest = sha(dst)
    meta = NEWLINE.join([digest, str(os.path.getsize(dst)), src, ""])
    write_text("rollback/scripts__kb_hook.py.pre.meta", meta)
    print("pin: " + digest)
    text = git_ls_files(KEEP, must_contain="scripts/kb_hook.py")
    write_text("index.before.txt", text)


def diff_inputs():
    pairs = [("inputs.before.txt", "inputs.after.txt"),
             ("inputs.before.sha256", "inputs.after.sha256"),
             ("kb-info.before.sha256", "kb-info.after.sha256")]
    for left, right in pairs:
        if read_text(left) != read_text(right):
            die("输入失配: " + left + " vs " + right)
    print("INPUTS_FROZEN_OK")


def compare():
    base = load_payload("baseline.json")
    aft = load_payload("after.json")
    allow = set(["parse_stats", "parse_detail", "skip_detail"])
    fail = []
    for key in sorted(set(base) | set(aft)):
        if key in allow:
            continue
        if base.get(key) != aft.get(key):
            fail.append("既有键变化: " + key)

    def badmap(doc):
        out = {}
        for item in doc.get("bad", []):
            out[item.get("file")] = "|".join(item.get("errors", []))
        return out

    lhs, rhs = badmap(base), badmap(aft)
    for key in sorted(set(lhs) | set(rhs)):
        if lhs.get(key) != rhs.get(key):
            fail.append("逐件 bad 差异: " + str(key))
    if fail:
        for line in fail:
            print("  - " + line)
        die("JSON 对照 FAIL")
    print("COMPARE_PASS")


def rc_compare():
    for tag in ["baseline", "after"]:
        load_payload(tag + ".json")
    b = read_text("baseline.json.rc").strip()
    a = read_text("after.json.rc").strip()
    eb = read_text("errset.before.json.rc").strip()
    ea = read_text("errset.after.json.rc").strip()
    print("整次 rc: baseline=" + b + " after=" + a)
    print("errset  : before=" + eb + " after=" + ea)
    for tag, val in [("baseline", b), ("after", a)]:
        if val not in ("0", "1"):
            die(tag + " 退出码非 0/1: " + val)
    if b != a:
        die("整次退出码不一致")
    if b == "1":
        base = load_payload("baseline.json")
        if not base.get("bad"):
            die("整次 rc=1 但 baseline 无既有 bad —— 疑似崩溃而非业务错误")
    if eb != "1":
        die("errset.before 非 1")
    if ea != "1":
        die("errset.after 非 1")
    print("RC_GATE_PASS")


def index_compare():
    text = git_ls_files(KEEP, must_contain="scripts/kb_hook.py")
    write_text("index.after.txt", text)
    if read_text("index.before.txt") != text:
        die("index 条目（mode/blob）变化")
    print("INDEX_UNCHANGED_OK")


def validate_baseline():
    """R18-r3 前移门：写 phase.before.done 之前先验 baseline 有效（仅此一验证前移）。"""
    doc = load_payload("baseline.json")
    rc = read_text("baseline.json.rc").strip()
    if rc not in ("0", "1"):
        die("baseline rc 非 0/1: " + rc)
    if rc == "1" and not doc.get("bad"):
        die("baseline rc=1 但无既有 bad —— 疑似崩溃而非业务错误")
    print("BASELINE_VALID rc=" + rc
          + " total=" + str(doc.get("total"))
          + " checked=" + str(doc.get("checked"))
          + " bad=" + str(len(doc.get("bad", [])))
          + " skipped=" + str(len(doc.get("skipped", []))))


def phase_before():
    if os.path.isfile(path("phase.before.done")):
        die("本目录已完成阶段 A，拒绝重复执行以免覆盖证据")
    assert_gates()
    pin()
    freeze("before")
    rc = errset("before")
    if rc != 1:
        die("errset.before 应为 1，实测 " + str(rc))
    capture("baseline", load_inputs())
    validate_baseline()
    write_text("phase.before.done", RUN_DIR + NEWLINE)
    print("PHASE_BEFORE_DONE")
    print("实施后执行: bash run.sh after " + RUN_DIR)


def assert_run_identity():
    """阶段门①：必须存在 phase.before.done，且其记录的 RUN 路径与本次传入一致。"""
    marker = path("phase.before.done")
    if not os.path.isfile(marker):
        die("缺 phase.before.done —— 该目录不是阶段 A 完成目录")
    recorded = read_text("phase.before.done").strip()
    if not recorded:
        die("phase.before.done 为空")
    if recorded != RUN_DIR:
        die("RUN 身份不符：marker 记录 [" + recorded + "] ≠ 传入 [" + RUN_DIR + "]")
    print("RUN_IDENTITY_OK")


def phase_after():
    """阶段门②：phase.after.started 已存在即拒绝（早失败后不得重复覆盖证据）。"""
    assert_run_identity()
    if os.path.isfile(path("phase.after.started")):
        die("phase.after.started 已存在 —— 拒绝重复执行（早失败材料必须保留，"
            "如需重跑请另建新目录）")
    if os.path.isfile(path("phase.after.done")):
        die("phase.after.done 已存在 —— 本次 after 已完成，拒绝重跑")
    for name in REQUIRED_BEFORE:
        if not os.path.isfile(path(name)):
            die("阶段 A 产物缺失: " + name)
    write_text("phase.after.started", RUN_DIR + NEWLINE)
    capture("after", load_inputs())
    rc = errset("after")
    if rc != 1:
        die("errset.after 应为 1，实测 " + str(rc))
    freeze("after")
    diff_inputs()
    compare()
    rc_compare()
    index_compare()
    write_text("phase.after.done", RUN_DIR + NEWLINE)
    print("PHASE_AFTER_DONE")


def main():
    global RUN_DIR
    if len(sys.argv) < 3:
        die("usage: run.py before|after <run_dir>")
    phase = sys.argv[1]
    RUN_DIR = os.path.abspath(sys.argv[2])
    if not os.path.isdir(RUN_DIR):
        die("RUN_DIR 不存在: " + RUN_DIR)
    print("RUN_DIR=" + RUN_DIR)
    if phase == "before":
        phase_before()
    elif phase == "after":
        phase_after()
    else:
        die("未知阶段 " + phase)


if __name__ == "__main__":
    main()
