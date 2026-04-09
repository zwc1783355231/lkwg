@echo off
setlocal
cd /d "%~dp0"
call D:\anaconda3\Scripts\activate.bat base
python desktop_map_tool.py
