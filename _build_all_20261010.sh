#!/bin/bash
# 2026-10-10 重打包全部 6 个 exe（悬停提示自适应定位的 web 改动要打进包才生效）
# 只做「挪走旧 exe + 构建」，分发复制在构建全部成功后再单独做。
set -u
cd "D:/Work Buddy 工作区/星座轨道实时查看器" || exit 1
PY="C:/Users/t2007/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
LOG="build_all_20261010.log"

{
  echo "======== 重打包开始 $(date '+%F %T') ========"
} > "$LOG"

step () {
  local title="$1"; shift
  echo "" >> "$LOG"
  echo "──────── $title  [$(date '+%T')]" >> "$LOG"
  "$@" >> "$LOG" 2>&1
  local rc=$?
  echo "   rc=$rc" >> "$LOG"
  echo "DONE | $title | rc=$rc"
  return 0
}

# 0) 把 dist 里要覆盖的旧 exe 挪走（本机安全策略会拦 PyInstaller 的覆盖删除）
mkdir -p ".整理备份/dist_prev_20261010"
mv dist/*.exe ".整理备份/dist_prev_20261010/" 2>/dev/null
echo "已挪走旧 exe：$(ls '.整理备份/dist_prev_20261010' | wc -l) 个" >> "$LOG"

# 1) CelesTrak 版（含过境预报）
step "1/6 CelesTrak 浏览器版" "$PY" -m PyInstaller --noconfirm --workpath build/wp_main   spec_onefile.spec
step "2/6 CelesTrak 桌面版"   "$PY" -m PyInstaller --noconfirm --workpath build/wp_main_d spec_desktop.spec

# 2) 不含过境预报版（前端用 web_nopass\）
step "3/6 无过境预报 浏览器版" env COV_WEB_SRC=web_nopass COV_EXE_NAME="星座轨道实时查看器_无过境预报" \
     "$PY" -m PyInstaller --noconfirm --workpath build/wp_nopass spec_onefile.spec
step "4/6 无过境预报 桌面版"   env COV_WEB_SRC=web_nopass COV_EXE_NAME="星座轨道实时查看器_无过境预报_桌面版" \
     "$PY" -m PyInstaller --noconfirm --workpath build/wp_nopass_d spec_desktop.spec

# 3) SpaceTrack 版（runtime hook 注入 COV_DATA_SRC=spacetrack）
step "5/6 SpaceTrack 浏览器版" env COV_EXE_NAME="星座轨道实时查看器_SpaceTrack版" COV_RUNTIME_HOOK="_st_data_src_hook.py" \
     "$PY" -m PyInstaller --noconfirm --workpath build_st_browser spec_onefile.spec
step "6/6 SpaceTrack 桌面版"   env COV_EXE_NAME="星座轨道实时查看器_SpaceTrack版_桌面版" COV_RUNTIME_HOOK="_st_data_src_hook.py" \
     "$PY" -m PyInstaller --noconfirm --workpath build_st_desktop spec_desktop.spec

echo "" >> "$LOG"
echo "dist 产物：" >> "$LOG"
ls -l dist/*.exe >> "$LOG" 2>&1
echo "======== 构建结束 $(date '+%F %T') ========" >> "$LOG"
exit 0
