@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 A股上市公司现金管理监测平台...
"C:\Users\Admin\AppData\Local\Python\bin\python.exe" start_platform.py
pause
