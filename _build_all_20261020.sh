#!/bin/bash
# 2026-10-20 重打包全部 6 个 exe
#   起因：给查看器加了手机端适配（web/mobile.css + web/mobile.js）与局域网访问（server.py --lan）。
#   exe 把 web/ 打进包 ⇒ 打包即冻结，不重打的话桌面版里没有 mobile.css / mobile.js，
#   手机连上来会拿到一份「没有响应式样式」的页面（页脚没有 ☰/ℹ，侧栏挤成一团）。
# 只做「挪走旧 exe + 构建 + 回填」，校验另跑。
set -u
cd "D:/Work Buddy 工作区/星座轨道实时查看器" || exit 1
PY="C:/Users/t2007/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
LOG="build_all_20261020.log"

{ echo "======== 重打包开始 $(date '+%F %T') ========"; } > "$LOG"

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

# 0) 旧 exe 挪走（PyInstaller 覆盖已存在文件会被本机安全策略拦）
mkdir -p ".整理备份/dist_prev_20261020"
mv dist/*.exe ".整理备份/dist_prev_20261020/" 2>/dev/null
echo "已挪走旧 exe：$(ls '.整理备份/dist_prev_20261020' 2>/dev/null | wc -l) 个" >> "$LOG"

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

# 4) 回填：dist → 工程根 / 不含过境预报版\
for f in 星座轨道实时查看器 星座轨道实时查看器_桌面版 \
         星座轨道实时查看器_SpaceTrack版 星座轨道实时查看器_SpaceTrack版_桌面版; do
  [ -f "dist/$f.exe" ] && cp -f "dist/$f.exe" "./$f.exe" && echo "回填 $f.exe" >> "$LOG"
done
for f in 星座轨道实时查看器_无过境预报 星座轨道实时查看器_无过境预报_桌面版; do
  [ -f "dist/$f.exe" ] && cp -f "dist/$f.exe" "不含过境预报版/$f.exe" && echo "回填 不含过境预报版/$f.exe" >> "$LOG"
done

echo "" >> "$LOG"
echo "dist 产物：" >> "$LOG"
ls -l dist/*.exe >> "$LOG" 2>&1
echo "======== 构建结束 $(date '+%F %T') ========" >> "$LOG"
exit 0
