@echo off
rem Rebuilds "Manga Reader.exe" after changing the source. Build tools stay in a temporary venv.
setlocal
cd /d "%~dp0.."
set WORK=%TEMP%\manga-reader-build
if exist "%WORK%" rmdir /s /q "%WORK%"
py -3 -m venv "%WORK%\venv" || goto :fail
"%WORK%\venv\Scripts\python" -m pip install --quiet pyinstaller pillow || goto :fail
"%WORK%\venv\Scripts\python" build_tools\make_icon.py static\icon.ico || goto :fail
rem Windows can't overwrite a running .exe but can rename it, so an open app isn't interrupted.
if exist "Manga Reader.exe" move /y "Manga Reader.exe" "Manga Reader.old" >nul
"%WORK%\venv\Scripts\pyinstaller" --noconfirm --onefile --windowed --name "Manga Reader" ^
  --icon "%CD%\static\icon.ico" --add-data "%CD%\static;static" ^
  --distpath "%CD%" --workpath "%WORK%\work" --specpath "%WORK%" manga_reader.py || goto :fail
rmdir /s /q "%WORK%"
echo Built "%CD%\Manga Reader.exe"
exit /b 0
:fail
echo Build failed.
exit /b 1
