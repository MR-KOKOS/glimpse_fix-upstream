@echo off
rem Builds fmodcheck.exe (32-bit, it loads the game's 32-bit FMOD DLLs). Needs VS 2022 Build Tools.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul || exit /b 1
cd /d "%~dp0"
cl /nologo /O2 /W3 /D_CRT_SECURE_NO_WARNINGS fmodcheck.c /Fo"%TEMP%\fmodcheck.obj" /Fe:fmodcheck.exe /link /SUBSYSTEM:CONSOLE
