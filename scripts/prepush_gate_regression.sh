#!/bin/bash
# prepush_gate_regression.sh — prepush-gate 回归矩阵（方案 a，2026-10-20）
#
# 用法：bash scripts/prepush_gate_regression.sh
# 覆盖：既有场景（单笔不误拦 / 未声明拒推 / 声明齐全放行 / 声明不全拒推 /
#       空范围放行 / 错锚拒推）+ 新增 refspec 场景（sha 形态：声明齐全放行 / 混入未声明拒推）。
# 全部在临时裸仓完成（git init --bare），绝不触碰真实远程。
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
PASS=0; FAIL=0

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

pass() { PASS=$((PASS+1)); echo "PASS  $1"; }
fail() { FAIL=$((FAIL+1)); echo "FAIL  $1"; }

expect_rc() {  # $1 name, $2 expect, $3 actual
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1（期望 rc=$2 实际 rc=$3）"; fi
}
expect_neq() {  # $1 name, $2 forbidden, $3 actual
  if [ "$2" != "$3" ]; then pass "$1"; else fail "$1（不应等于 rc=$2）"; fi
}

# 解释器探测（hook 同款逻辑）
if command -v pwsh >/dev/null 2>&1; then PSOK=1
elif command -v powershell >/dev/null 2>&1; then PSOK=1
else PSOK=0; fi

# 构造 fixture：bare remote + work 仓库（HEAD 含 r0 + N 个提交）；输出 work 目录路径
fixture() {  # $1 name, $2 commit_count
  local name="$1" n="$2" d="$TMP/$1"
  mkdir -p "$d"
  git init -q --bare "$d/remote.git"
  git -C "$d/remote.git" symbolic-ref HEAD refs/heads/main
  git init -q "$d/work"
  local w="$d/work"
  git -C "$w" symbolic-ref HEAD refs/heads/main
  git -C "$w" config core.autocrlf false
  git -C "$w" config user.email test@example.com
  git -C "$w" config user.name test
  git -C "$w" config core.hooksPath .githooks
  mkdir -p "$w/.githooks" "$w/scripts"
  cp "$REPO/.githooks/pre-push" "$w/.githooks/pre-push"
  chmod +x "$w/.githooks/pre-push"
  cp "$REPO/scripts/prepush-gate.ps1" "$w/scripts/prepush-gate.ps1"
  echo r0 > "$w/f"
  git -C "$w" add f
  git -C "$w" commit -qm r0
  git -C "$w" remote add origin "$d/remote.git"
  git -C "$w" push -q origin main >/dev/null 2>&1
  local i
  i=1
  while [ "$i" -le "$n" ]; do
    echo "c$i" > "$w/f$i"
    git -C "$w" add "f$i"
    git -C "$w" commit -qm "c$i"
    i=$((i + 1))
  done
  echo "$w"
}

sha_of() {  # $1 work, $2 序号（0=r0,1=c1,...）
  local w="$1" idx="$2"
  git -C "$w" rev-list --reverse HEAD | sed -n "$((idx + 1))p"
}

remote_tip() {  # $1 work dir
  local w="$1"
  git -C "$(dirname "$w")/remote.git" rev-parse refs/heads/main
}

do_push() {  # $1 work, rest = push args → 输出 rc
  local w="$1"; shift
  ( cd "$w" && git push "$@" ) >/dev/null 2>&1
  echo $?
}

push_decl() {  # $1 work, $2 声明列表, rest = push args → 输出 rc
  local w="$1" decl="$2"; shift 2
  ( cd "$w" && QS_PUSH_BUNDLE_ALLOWALSO="$decl" git push "$@" ) >/dev/null 2>&1
  echo $?
}

gate_rc() {  # $1 work, rest = gate args → 输出 rc
  local w="$1"; shift
  ( cd "$w" && "$PS_BIN" -NoProfile -ExecutionPolicy Bypass -File scripts/prepush-gate.ps1 "$@" ) >/dev/null 2>&1
  echo $?
}

PS_BIN=""
if command -v pwsh >/dev/null 2>&1; then PS_BIN=pwsh
elif command -v powershell >/dev/null 2>&1; then PS_BIN=powershell; fi

if [ "$PSOK" -eq 0 ]; then
  echo "SKIP  未找到 pwsh/powershell，无法执行门回归"
  exit 0
fi

# S1 单笔正常推送（HEAD=c1，origin/main=r0）→ 不误拦
w=$(fixture s1 1); r0=$(sha_of "$w" 0); c1=$(sha_of "$w" 1)
rc=$(do_push "$w" origin main)
expect_rc s1_single_push 0 "$rc"
tip=$(remote_tip "$w")
[ "$tip" = "$c1" ] && pass s1_remote_updated || fail s1_remote_updated

# S2 两笔未声明（HEAD=c2，origin/main=r0）→ 拒推
w=$(fixture s2 2); r0=$(sha_of "$w" 0); c1=$(sha_of "$w" 1); c2=$(sha_of "$w" 2)
rc=$(do_push "$w" origin main)
expect_neq s2_undeclared_rejected 0 "$rc"
tip=$(remote_tip "$w")
[ "$tip" = "$r0" ] && pass s2_remote_unchanged || fail s2_remote_unchanged

# S3 声明齐全（HEAD=c2，origin/main=r0，声明 c1）→ 放行 + 留痕
w=$(fixture s3 2); r0=$(sha_of "$w" 0); c1=$(sha_of "$w" 1); c2=$(sha_of "$w" 2)
rc=$(push_decl "$w" "$c1" origin main)
expect_rc s3_declared_pass 0 "$rc"
tip=$(remote_tip "$w")
[ "$tip" = "$c2" ] && pass s3_remote_updated || fail s3_remote_updated
[ -f "$w/docs/handoff/push-bundle-rulings.log" ] && pass s3_log_written || fail s3_log_written

# S4 声明不全（HEAD=c3，声明 c1 缺 c2）→ 拒推
w=$(fixture s4 3); r0=$(sha_of "$w" 0); c1=$(sha_of "$w" 1); c2=$(sha_of "$w" 2)
rc=$(push_decl "$w" "$c1" origin main)
expect_neq s4_partial_decl_rejected 0 "$rc"

# S5 空范围（直调 gate，base=tip）→ PASS
w=$(fixture s5 1); c1=$(sha_of "$w" 1)
rc=$(gate_rc "$w" -Commit "$c1" -LocalSha "$c1" -RemoteSha "$c1")
expect_rc s5_empty_range_pass 0 "$rc"

# S6 错锚（锚不在推送范围）→ 拒推
w=$(fixture s6 5); r0=$(sha_of "$w" 0); c2=$(sha_of "$w" 2); c5=$(sha_of "$w" 5)
rc=$(gate_rc "$w" -Commit "$c5" -LocalSha "$c2" -RemoteSha "$r0")
expect_rc s6_bad_anchor_rejected 9 "$rc"

# S7 refspec sha 形态 · 声明齐全（HEAD=c5，push c2:main，声明 c1）→ 放行
#    （修复前用 origin/main..HEAD 会误把 c3..c5 计入而拒推）
w=$(fixture s7 5); r0=$(sha_of "$w" 0); c1=$(sha_of "$w" 1); c2=$(sha_of "$w" 2)
rc=$(push_decl "$w" "$c1" origin "$c2:main")
expect_rc s7_refspec_declared_pass 0 "$rc"
tip=$(remote_tip "$w")
[ "$tip" = "$c2" ] && pass s7_remote_updated || fail s7_remote_updated

# S8 refspec sha 形态 · 混入未声明（HEAD=c5，push c3:main，声明 c1 缺 c2）→ 拒推
w=$(fixture s8 5); r0=$(sha_of "$w" 0); c1=$(sha_of "$w" 1); c2=$(sha_of "$w" 2); c3=$(sha_of "$w" 3)
rc=$(push_decl "$w" "$c1" origin "$c3:main")
expect_neq s8_refspec_undeclared_rejected 0 "$rc"

echo "------------------------------------------"
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
