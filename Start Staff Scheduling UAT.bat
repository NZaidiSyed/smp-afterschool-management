@echo off
cd /d "%~dp0"
echo Starting the local Staff Scheduling UAT. Keep this window open.
py run_staff_scheduling_uat.py
if errorlevel 1 echo UAT could not start. Please share the error above for troubleshooting.
pause
