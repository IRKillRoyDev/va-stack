@echo off
rem Double-click on Windows (developer fallback): open VA Stack from .venv.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
va-stack %*
