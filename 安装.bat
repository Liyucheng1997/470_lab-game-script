@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在创建运行环境...
where uv >nul 2>nul
if %errorlevel%==0 (
  uv venv --python 3.13 .venv
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt
) else (
  py -3 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo 正在从游戏文件提取资料...
.venv\Scripts\python.exe build_game_data.py
echo.
echo 好了！双击「启动助手.vbs」开始使用。
pause
