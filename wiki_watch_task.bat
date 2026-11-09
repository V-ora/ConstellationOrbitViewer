@echo off
chcp 65001 >nul
cd /d "%~dp0"
set GIT=D:\LenovoSoftstore\Install\WorkBuddy\resources\vendor\PortableGit\mingw64\bin\git.exe
set FLAG=%1
echo [%date% %time%] wiki watch start %FLAG% >> cache\wiki_task.log
python -X utf8 wiki_watch.py %FLAG% >> cache\wiki_task.log 2>&1
if errorlevel 1 goto done
"%GIT%" add -A cache data >> cache\wiki_task.log 2>&1
"%GIT%" diff --cached --quiet
if not errorlevel 1 (
  echo no change, skip commit >> cache\wiki_task.log
  goto done
)
python -X utf8 make_qf_html.py >> cache\wiki_task.log 2>&1
python -X utf8 make_gw_html.py >> cache\wiki_task.log 2>&1
python -X utf8 sync_web_live.py >> cache\wiki_task.log 2>&1
copy /y "web\raise\千帆星座轨道爬升交互大图.html" "docs\web\raise\" >nul 2>&1
copy /y "web\raise\国网低轨卫星轨道爬升交互大图.html" "docs\web\raise\" >nul 2>&1
"%GIT%" add -A cache data web/raise docs >> cache\wiki_task.log 2>&1
"%GIT%" commit -m "wiki watch: task table / charts auto update" >> cache\wiki_task.log 2>&1
"%GIT%" push >> cache\wiki_task.log 2>&1
:done
echo [%date% %time%] wiki watch end >> cache\wiki_task.log