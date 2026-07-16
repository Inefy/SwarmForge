@echo off
REM Overnight self-play training session (default 8 hours). Ctrl+C to stop early.
py -3 train.py --hours 8 %*
pause
