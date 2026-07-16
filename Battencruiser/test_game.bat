@echo off
REM Test game vs the built-in Elite AI (random race each run is a good workout:
REM change --race to terran / protoss / zerg / random).
py -3 run.py --race random --difficulty veryhard %*
pause
