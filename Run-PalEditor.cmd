@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%project\editor"
if defined PALADMIN_PYTHON (set "PYTHON=%PALADMIN_PYTHON%") else (set "PYTHON=python")
"%PYTHON%" -m pal_editor.gui %*
endlocal
