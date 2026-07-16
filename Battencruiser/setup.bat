@echo off
echo Installing python-sc2 (burnysc2)...
py -3 -m pip install --upgrade pip
py -3 -m pip install --upgrade burnysc2
echo.
echo Done. Next steps:
echo   1. Make sure StarCraft II is installed (free via Battle.net).
echo   2. Download ladder maps from https://aiarena.net/wiki/maps/
echo      and unzip them into "C:\Program Files (x86)\StarCraft II\Maps"
echo      (create the Maps folder if it does not exist).
echo   3. Double-click test_game.bat to watch the bot play.
pause
