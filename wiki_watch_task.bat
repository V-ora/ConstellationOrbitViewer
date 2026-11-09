@echo off
chcp 65001 >nul
cd /d "D:\Work Buddy ???\?????????"
set FLAG=%1
echo [%date% %time%] ???????? %FLAG% >> cache\wiki_task.log
python -X utf8 wiki_watch.py %FLAG% >> cache\wiki_task.log 2>&1
if errorlevel 1 goto done
git add -A cache data >> cache\wiki_task.log 2>&1
git diff --cached --quiet
if errorlevel 1 (
  python -X utf8 make_qf_html.py >> cache\wiki_task.log 2>&1
  python -X utf8 make_gw_html.py >> cache\wiki_task.log 2>&1
  python -X utf8 sync_web_live.py >> cache\wiki_task.log 2>&1
  copy /y "web\raise\????????????.html" "docs\web\raise\" >nul 2>&1
  copy /y "web\raise\??????????????.html" "docs\web\raise\" >nul 2>&1
  git add -A cache data web/raise docs >> cache\wiki_task.log 2>&1
  git commit -m "??????????/?????? %date% %time%" >> cache\wiki_task.log 2>&1
  git push >> cache\wiki_task.log 2>&1
) else (
  echo ??????? >> cache\wiki_task.log
)
:done
echo [%date% %time%] ???? >> cache\wiki_task.log