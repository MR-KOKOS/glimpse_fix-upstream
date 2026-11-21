@echo off
rem Builds and runs the offline harness (x86, VS 2022 Build Tools) on the DLL's own src\vark.cpp and
rem src\iohook.cpp. Run "python -I test_vark.py ..." first.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
cd /d "%~dp0"
if not exist test_out mkdir test_out
cl /nologo /O2 /MT /W4 /EHsc /D_CRT_SECURE_NO_WARNINGS /I..\..\..\src /Fotest_out\ /Fetest_out\test_vark.exe test_vark.cpp ..\..\..\src\vark.cpp ..\..\..\src\iohook.cpp || exit /b 1
test_out\test_vark.exe test_out\expect.txt
