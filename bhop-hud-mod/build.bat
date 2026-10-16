@echo off
rem ===========================================================================
rem  Deadlock :: Bhop Trainer HUD  --  one-click mod builder for Windows
rem
rem  Double-click it and it does everything by itself:
rem    * finds Python 3.9+ (installs it via winget when missing),
rem    * finds the installed Deadlock (Steam registry + libraryfolders.vdf),
rem    * finds CSDK 12 with resourcecompiler.exe and, if it is missing,
rem      downloads and unpacks Reduced CSDK 12 all by itself,
rem    * builds the VPK and the Deadlock Mod Manager package,
rem    * offers to stage / install / verify the mod.
rem
rem  Options (all optional):
rem    build.bat --game-path "D:\Steam\steamapps\common\Deadlock"
rem               --csdk "C:\Deadlock SDK"
rem               --csdk-install-dir "C:\DeadlockModding"  (auto-downloaded CSDK)
rem               --csdk-url "https://..."   (your own CSDK archive link)
rem               --no-csdk-download         (never download, ask for the path)
rem               --csdk-redownload          (ignore the cached archive)
rem               --hud-xml "C:\hud.xml"   (vanilla hud.xml when the game is absent)
rem               --version 1.2.0
rem               --steps prepare,compile,pack,dmm,report
rem               --out "D:\dist"
rem               --clean     (delete build\ and dist\ first)
rem               --test      (run the builder's unit tests first)
rem               -y          (no questions, for CI)
rem  Unknown options are forwarded to tools\build_mod.py as-is.
rem
rem  NOTE: this file is intentionally pure ASCII (no codepage switching), so it
rem  looks the same on any Windows locale. The builder it runs prints Russian.
rem ===========================================================================

setlocal
title Deadlock :: Bhop Trainer HUD - mod builder

rem --- locate the mod: this bat's folder, or bhop-hud-mod next to it ----------
set "MOD_DIR=%~dp0"
if "%MOD_DIR:~-1%"=="\" set "MOD_DIR=%MOD_DIR:~0,-1%"

if exist "%MOD_DIR%\tools\build_mod.py" goto :root_ok
if exist "%MOD_DIR%\bhop-hud-mod\tools\build_mod.py" set "MOD_DIR=%MOD_DIR%\bhop-hud-mod"
if exist "%MOD_DIR%\tools\build_mod.py" goto :root_ok
if exist "%MOD_DIR%\..\bhop-hud-mod\tools\build_mod.py" for %%i in ("%MOD_DIR%\..\bhop-hud-mod") do set "MOD_DIR=%%~fi"
if not exist "%MOD_DIR%\tools\build_mod.py" goto :no_sources

:root_ok
set "TOOLS_DIR=%MOD_DIR%\tools"
set "BUILD_PY=%TOOLS_DIR%\build_mod.py"
set "SETUP_PY=%TOOLS_DIR%\csdk_setup.py"
set "INSTALL_PY=%TOOLS_DIR%\install_dmm.py"
set "DIST_DIR=%MOD_DIR%\dist"
set "BUILD_DIR=%MOD_DIR%\build"

rem --- defaults --------------------------------------------------------------
set "PY_CMD="
set "GAME_DIR="
set "CSDK_DIR="
set "ARG_GAME="
set "ARG_CSDK="
set "ARG_HUD="
set "ARG_STEPS="
set "ARG_OUT="
set "ARG_VERSION="
set "ARG_EXTRA="
set "ARG_CSDK_URL="
set "ARG_CSDK_DIR="
set "DO_CSDK_REDOWNLOAD=0"
set "NO_CSDK_DOWNLOAD=0"
set "CSDK_ROOT_FILE=%TEMP%\deadlock-bhop-csdk-root.txt"
if not defined TEMP set "CSDK_ROOT_FILE=%MOD_DIR%\build\csdk-root.txt"
set "ALLOW_UNCOMPILED=0"
set "DO_CLEAN=0"
set "DO_TEST=0"
set "NONINTERACTIVE=0"
set "GAME_ARG="
set "DIST_ARG="
set "STEAM_PATH="
set "CSDK_URL=https://deadlockmodding.pages.dev/modding-tools/csdk-12"

rem --- parse arguments -------------------------------------------------------
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--game-path" set "ARG_GAME=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--csdk" set "ARG_CSDK=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--csdk-url" set "ARG_CSDK_URL=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--csdk-install-dir" set "ARG_CSDK_DIR=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--csdk-redownload" set "DO_CSDK_REDOWNLOAD=1" & shift & goto :parse_args
if /i "%~1"=="--no-csdk-download" set "NO_CSDK_DOWNLOAD=1" & shift & goto :parse_args
if /i "%~1"=="--hud-xml" set "ARG_HUD=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--steps" set "ARG_STEPS=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--out" set "ARG_OUT=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--version" set "ARG_VERSION=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="-y" set "NONINTERACTIVE=1" & shift & goto :parse_args
if /i "%~1"=="--yes" set "NONINTERACTIVE=1" & shift & goto :parse_args
if /i "%~1"=="--clean" set "DO_CLEAN=1" & shift & goto :parse_args
if /i "%~1"=="--test" set "DO_TEST=1" & shift & goto :parse_args
if /i "%~1"=="--allow-uncompiled" set "ALLOW_UNCOMPILED=1" & shift & goto :parse_args
if /i "%~1"=="/?" goto :usage
if /i "%~1"=="--help" goto :usage
if /i "%~1"=="-h" goto :usage
set "ARG_EXTRA=%ARG_EXTRA% "%~1""
shift
goto :parse_args

:args_done
if defined ARG_OUT set "DIST_DIR=%ARG_OUT%"
if defined ARG_OUT set "DIST_ARG=--dist "%ARG_OUT%""

echo.
echo ============================================================
echo   Deadlock :: Bhop Trainer HUD - build
echo ============================================================
echo   Mod:  %MOD_DIR%
echo   Out:  %DIST_DIR%
echo.

if "%DO_CLEAN%"=="1" call :do_clean

rem --- Python ----------------------------------------------------------------
:python_search
call :find_python
if defined PY_CMD goto :python_found
if "%NONINTERACTIVE%"=="1" goto :python_auto
call :offer_python_install
call :find_python
if defined PY_CMD goto :python_found

:python_auto
rem -y means no questions, so the interpreter is installed without asking
echo [i] -y is on: installing Python 3.12 via winget...
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :winget_failed
echo.
echo [i] Refreshing PATH in this window and looking for Python again...
call :refresh_path
call :find_python
if defined PY_CMD goto :python_found

:python_missing
echo.
echo [!] Python 3.9+ not found - cannot build.
echo.
echo     Options:
echo       1) winget install -e --id Python.Python.3.12
echo       2) https://www.python.org/downloads/  (tick "Add python.exe to PATH")
echo.
goto :fail

:python_found
echo [i] Python:    %PY_CMD%

if "%DO_TEST%"=="1" call :run_tests

rem --- Deadlock --------------------------------------------------------------
call :resolve_game
if defined GAME_DIR goto :game_found
if defined ARG_HUD goto :game_found
goto :game_missing

:game_found
if defined GAME_DIR set "GAME_ARG=--game-path "%GAME_DIR%""
if defined GAME_DIR echo [i] Deadlock:  %GAME_DIR%

rem --- CSDK ------------------------------------------------------------------
call :resolve_csdk
if defined CSDK_DIR goto :csdk_found

if "%NO_CSDK_DOWNLOAD%"=="1" goto :csdk_menu_manual
if "%NONINTERACTIVE%"=="1" goto :csdk_auto_try

:csdk_menu
echo.
echo [!] CSDK 12 with resourcecompiler.exe was not found.
echo     The Deadlock engine will not load the mod without it: it needs
echo     compiled .vxml_c / .vcss_c, not raw .xml / .css.
echo.
echo     The builder can download and unpack Reduced CSDK 12 itself:
echo     a large archive, into a folder outside OneDrive / Desktop / Documents.
echo.
echo     [1] Download and install CSDK 12 automatically  (recommended)
echo     [2] Type the CSDK path by hand (you can drag the folder here)
echo     [3] Open the CSDK download page
echo     [4] Build anyway, sources only (--allow-uncompiled: such a mod does
echo         not work in game and no DMM package is produced)
echo     [0] Quit
echo.
set "ANS="
set /p "ANS=    Choice [1]: "
if not defined ANS set "ANS=1"
if "%ANS%"=="1" goto :csdk_auto_try
if "%ANS%"=="2" goto :csdk_manual
if "%ANS%"=="3" goto :csdk_open_url
if "%ANS%"=="4" goto :csdk_allow_uncompiled
if "%ANS%"=="0" goto :fail
goto :csdk_menu

:csdk_menu_manual
if "%NONINTERACTIVE%"=="1" goto :csdk_missing_exit
echo.
echo [!] CSDK 12 with resourcecompiler.exe was not found.
echo     Auto-download is switched off (--no-csdk-download).
echo.
echo     Manual page: %CSDK_URL%
echo     (do not unpack it into OneDrive / Desktop / Documents - breaks perms)
echo.
echo     [1] Type the CSDK path by hand (you can drag the folder here)
echo     [2] Open the CSDK download page
echo     [3] Build anyway, sources only (--allow-uncompiled: such a mod does
echo         not work in game and no DMM package is produced)
echo     [0] Quit
echo.
set "ANS="
set /p "ANS=    Choice: "
if "%ANS%"=="1" goto :csdk_manual
if "%ANS%"=="2" goto :csdk_open_url
if "%ANS%"=="3" goto :csdk_allow_uncompiled
if "%ANS%"=="0" goto :fail
goto :csdk_menu_manual

:csdk_auto_try
call :csdk_auto
if defined CSDK_DIR goto :csdk_found
if "%NONINTERACTIVE%"=="1" goto :fail
if "%NO_CSDK_DOWNLOAD%"=="1" goto :csdk_menu_manual
goto :csdk_menu

:csdk_auto
echo.
echo [i] Downloading and unpacking Reduced CSDK 12 - it is a large archive,
echo     so allow some time; a broken download is resumed, not restarted.
echo.
set "CSDK_DIR="
set "CSDK_SETUP_ARGS=--root-file "%CSDK_ROOT_FILE%""
if defined ARG_CSDK_DIR set "CSDK_SETUP_ARGS=%CSDK_SETUP_ARGS% --install-dir "%ARG_CSDK_DIR%""
if defined ARG_CSDK_URL set "CSDK_SETUP_ARGS=%CSDK_SETUP_ARGS% --url "%ARG_CSDK_URL%""
if "%DO_CSDK_REDOWNLOAD%"=="1" set "CSDK_SETUP_ARGS=%CSDK_SETUP_ARGS% --redownload"
if exist "%CSDK_ROOT_FILE%" del /q "%CSDK_ROOT_FILE%" >nul 2>nul
%PY_CMD% "%SETUP_PY%" %CSDK_SETUP_ARGS%
set "_CSDK_TMP="
if exist "%CSDK_ROOT_FILE%" set /p "_CSDK_TMP=" < "%CSDK_ROOT_FILE%"
if not defined _CSDK_TMP goto :csdk_auto_failed
call :check_csdk "%_CSDK_TMP%"
if defined CSDK_DIR goto :eof

:csdk_auto_failed
echo.
echo [!] Automatic CSDK setup failed. Unpack Reduced CSDK 12 by hand from
echo     %CSDK_URL%
echo     (not into OneDrive / Desktop / Documents) and run build.bat again:
echo     it finds the compiler on its own. Or type the folder by hand.
goto :eof

:csdk_manual
echo.
echo     Paste the path to the unpacked CSDK (it must contain
echo     game\bin_cs2\win64\resourcecompiler.exe):
echo.
set "ANS="
set /p "ANS=    CSDK: "
set "ANS=%ANS:"=%"
if not defined ANS goto :csdk_menu
call :check_csdk "%ANS%"
if defined CSDK_DIR goto :csdk_found
echo.
echo     No resourcecompiler.exe under that path.
if "%NO_CSDK_DOWNLOAD%"=="1" goto :csdk_menu_manual
goto :csdk_menu

:csdk_open_url
start "" "%CSDK_URL%"
if "%NO_CSDK_DOWNLOAD%"=="1" goto :csdk_menu_manual
goto :csdk_menu

:csdk_allow_uncompiled
set "ALLOW_UNCOMPILED=1"
echo.
echo [i] Building sources-only artifacts (they do not work in game).
goto :csdk_found

:csdk_missing_exit
echo [!] CSDK not found, auto-download is off and -y forbids questions.
goto :fail

:csdk_found
if defined CSDK_DIR echo [i] CSDK:      %CSDK_DIR%
if /i not "%CSDK_DIR:OneDrive=%"=="%CSDK_DIR%" echo [!] Warning: CSDK sits in OneDrive - the compiler often breaks there.

rem --- build -----------------------------------------------------------------
:do_build
echo.
echo ============================================================
echo   Building
echo ============================================================
echo.

set "BUILD_ARGS="
if defined GAME_DIR set "BUILD_ARGS=%BUILD_ARGS% --game-path "%GAME_DIR%""
if defined ARG_HUD set "BUILD_ARGS=%BUILD_ARGS% --hud-xml "%ARG_HUD%""
if defined CSDK_DIR set "BUILD_ARGS=%BUILD_ARGS% --csdk "%CSDK_DIR%""
if "%NO_CSDK_DOWNLOAD%"=="1" set "BUILD_ARGS=%BUILD_ARGS% --no-auto-csdk"
if defined ARG_CSDK_DIR set "BUILD_ARGS=%BUILD_ARGS% --csdk-install-dir "%ARG_CSDK_DIR%""
if defined ARG_CSDK_URL set "BUILD_ARGS=%BUILD_ARGS% --csdk-url "%ARG_CSDK_URL%""
if "%DO_CSDK_REDOWNLOAD%"=="1" set "BUILD_ARGS=%BUILD_ARGS% --csdk-redownload"
if "%DO_CLEAN%"=="1" set "BUILD_ARGS=%BUILD_ARGS% --clean"
if defined ARG_STEPS set "BUILD_ARGS=%BUILD_ARGS% --steps "%ARG_STEPS%""
if defined ARG_OUT set "BUILD_ARGS=%BUILD_ARGS% --out "%ARG_OUT%""
if defined ARG_VERSION set "BUILD_ARGS=%BUILD_ARGS% --version "%ARG_VERSION%""
if "%ALLOW_UNCOMPILED%"=="1" set "BUILD_ARGS=%BUILD_ARGS% --allow-uncompiled"

%PY_CMD% "%BUILD_PY%"%BUILD_ARGS%%ARG_EXTRA%
set "BUILD_RC=%ERRORLEVEL%"

if "%BUILD_RC%"=="0" goto :build_ok
if "%BUILD_RC%"=="3" goto :build_needs_compile
if "%BUILD_RC%"=="130" goto :build_cancel

:build_failed
echo.
echo [X] Build failed (exit code %BUILD_RC%).
echo     Details are in %DIST_DIR%\BUILD_REPORT.md
goto :fail

:build_cancel
echo.
echo [X] Build cancelled.
goto :fail

:build_needs_compile
echo.
echo [!] The build stopped at the compile step (exit code 3).
echo     Check that CSDK is unpacked and that it contains
echo     game\bin_cs2\win64\resourcecompiler.exe
if "%NONINTERACTIVE%"=="1" goto :fail
echo.
set "ANS="
set /p "ANS=    Download/install CSDK and retry? [Y/n/s(ources only)]: "
if /i "%ANS%"=="s" set "ALLOW_UNCOMPILED=1"
if /i "%ANS%"=="s" goto :do_build
if /i "%ANS%"=="n" goto :fail
if "%NO_CSDK_DOWNLOAD%"=="1" goto :fail
call :csdk_auto
if not defined CSDK_DIR goto :fail
goto :do_build

:build_ok
echo.
echo ============================================================
echo   Done
echo ============================================================
echo.
echo   Artifacts are in:
echo     %DIST_DIR%
echo.
echo     bhop_trainer_dir.vpk   - the addon itself
echo     dmm-bundle\            - package for Deadlock Mod Manager
echo     bhop_trainer-dmm.zip   - the same package as a zip
echo     BUILD_REPORT.md        - build report
echo     SHA256SUMS.txt         - checksums
echo.
if "%ALLOW_UNCOMPILED%"=="1" echo [!] Built without the compiler: it will not work in game.
if "%ALLOW_UNCOMPILED%"=="1" echo.
if "%NONINTERACTIVE%"=="1" goto :done

:menu
echo   What next:
echo     [1] Open the DMM package folder (import into Mod Manager)
echo     [2] Install the mod into the game directly, no manager
echo     [3] Copy the trainer .cfg files into game\citadel\cfg
echo     [4] Verify the installation
echo     [5] Open the build report
echo     [0] Quit
echo.
set "ANS="
set /p "ANS=    Choice: "
if "%ANS%"=="1" goto :act_stage
if "%ANS%"=="2" goto :act_direct
if "%ANS%"=="3" goto :act_cfg
if "%ANS%"=="4" goto :act_verify
if "%ANS%"=="5" goto :act_report
if "%ANS%"=="0" goto :done
echo     Did not get that, try again.
echo.
goto :menu

:act_stage
echo.
%PY_CMD% "%INSTALL_PY%" stage --open %GAME_ARG% %DIST_ARG%
echo.
goto :menu

:act_direct
echo.
%PY_CMD% "%INSTALL_PY%" direct %GAME_ARG% %DIST_ARG%
echo.
goto :menu

:act_cfg
echo.
%PY_CMD% "%INSTALL_PY%" cfg %GAME_ARG%
echo.
goto :menu

:act_verify
echo.
%PY_CMD% "%INSTALL_PY%" verify %GAME_ARG% %DIST_ARG%
echo.
goto :menu

:act_report
if exist "%DIST_DIR%\BUILD_REPORT.md" start "" notepad "%DIST_DIR%\BUILD_REPORT.md"
if not exist "%DIST_DIR%\BUILD_REPORT.md" echo     No report at: %DIST_DIR%\BUILD_REPORT.md
echo.
goto :menu


rem ==========================================================================
rem  Subroutines
rem ==========================================================================

rem --- find Python -----------------------------------------------------------
:find_python
set "PY_CMD="
call :try_py py -3
if defined PY_CMD goto :eof
call :try_py py ""
if defined PY_CMD goto :eof
call :try_py python ""
if defined PY_CMD goto :eof
call :try_py python3 ""
if defined PY_CMD goto :eof
rem winget and the official installer drop Python here even if PATH is stale
for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try_py_path "%%P\python.exe" ""
if defined PY_CMD goto :eof
if exist "%SystemDrive%\Python312\python.exe" call :try_py_path "%SystemDrive%\Python312\python.exe" ""
if defined PY_CMD goto :eof
if exist "%SystemDrive%\Python311\python.exe" call :try_py_path "%SystemDrive%\Python311\python.exe" ""
if defined PY_CMD goto :eof
if exist "%SystemDrive%\Python310\python.exe" call :try_py_path "%SystemDrive%\Python310\python.exe" ""
goto :eof

:try_py
rem %~1 - exe name (py / python / python3), %~2 - extra switch ("-3" or "")
set "_py_name=%~1"
set "_py_extra=%~2"
set "_py_full="
for /f "delims=" %%i in ('where %_py_name% 2^>nul') do call :try_py_path "%%i" "%_py_extra%"
goto :eof

:try_py_path
rem %~1 - full path to python.exe, %~2 - extra switch
if defined PY_CMD goto :eof
set "_py_full=%~1"
if not defined _py_full goto :eof
if not exist "%_py_full%" goto :eof
rem the Microsoft Store python.exe is only a stub - it does not count
if /i not "%_py_full:WindowsApps=%"=="%_py_full%" goto :eof
"%_py_full%" %~2 -c "import sys; sys.exit(0 if sys.version_info[0] == 3 and sys.version_info[1] >= 9 else 1)" >nul 2>nul
if errorlevel 1 goto :eof
rem no parenthesised block here: the path may contain "Program Files (x86)"
if "%~2"=="" set "PY_CMD=%_py_full%"
if "%~2"=="" goto :eof
set "PY_CMD=%_py_full% %~2"
goto :eof

:offer_python_install
echo.
echo [!] Python 3.9+ not found.
echo.
echo     [1] Install Python 3.12 myself via winget
echo     [2] Open the Python download page
echo     [0] Quit
echo.
set "ANS="
set /p "ANS=    Choice: "
if "%ANS%"=="1" goto :install_python
if "%ANS%"=="2" goto :open_python_url
if "%ANS%"=="0" goto :fail
goto :offer_python_install

:open_python_url
start "" "https://www.python.org/downloads/windows/"
goto :offer_python_install

:install_python
echo.
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
echo [i] Installing Python.Python.3.12 with winget...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :winget_failed
echo.
echo [i] Refreshing PATH in this window and looking for Python again...
call :refresh_path
goto :eof

:no_winget
echo [!] winget is not available - install Python manually:
echo     https://www.python.org/downloads/windows/
echo     (tick "Add python.exe to PATH" during setup)
echo.
echo     Then close this window and run build.bat again.
goto :fail

:winget_failed
echo [!] winget could not install Python (exit code %ERRORLEVEL%).
echo     Install it manually: https://www.python.org/downloads/windows/
goto :fail

:refresh_path
set "_sys_path="
set "_usr_path="
for /f "tokens=2,*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "_sys_path=%%B"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "_usr_path=%%B"
if defined _sys_path set "PATH=%_sys_path%;%PATH%"
if defined _usr_path set "PATH=%_usr_path%;%PATH%"
goto :eof

rem --- clean -----------------------------------------------------------------
:do_clean
echo [i] Removing working directories...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
echo     done.
echo.
goto :eof

rem --- tests -----------------------------------------------------------------
:run_tests
echo [i] Running the builder's unit tests...
%PY_CMD% -m unittest discover -s "%MOD_DIR%\tests" -t "%MOD_DIR%" -p "test_*.py"
if errorlevel 1 goto :tests_failed
echo     tests passed.
echo.
goto :eof

:tests_failed
echo.
echo [X] Tests failed - stopping before the build.
goto :fail

rem --- find Deadlock ---------------------------------------------------------
:resolve_game
set "GAME_DIR="
if defined ARG_GAME call :check_game_dir "%ARG_GAME%"
if defined GAME_DIR goto :eof
if defined DEADLOCK_DIR call :check_game_dir "%DEADLOCK_DIR%"
if defined GAME_DIR goto :eof
if defined DEADLOCK_GAME_DIR call :check_game_dir "%DEADLOCK_GAME_DIR%"
if defined GAME_DIR goto :eof
if defined DEADLOCK_PATH call :check_game_dir "%DEADLOCK_PATH%"
if defined GAME_DIR goto :eof

rem Steam from the registry
set "STEAM_PATH="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Valve\Steam" /v SteamPath 2^>nul') do set "STEAM_PATH=%%B"
if not defined STEAM_PATH for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\WOW6432Node\Valve\Steam" /v InstallPath 2^>nul') do set "STEAM_PATH=%%B"
if not defined STEAM_PATH for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Valve\Steam" /v InstallPath 2^>nul') do set "STEAM_PATH=%%B"
if not defined STEAM_PATH for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Valve\Steam" /v InstallPath 2^>nul') do set "STEAM_PATH=%%B"

if defined STEAM_PATH call :check_game_dir "%STEAM_PATH%\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
if defined STEAM_PATH call :scan_libraryfolders "%STEAM_PATH%\steamapps\libraryfolders.vdf"
if defined GAME_DIR goto :eof
if defined STEAM_PATH call :scan_libraryfolders "%STEAM_PATH%\config\libraryfolders.vdf"
if defined GAME_DIR goto :eof

rem usual places on every drive
for %%D in (C D E F G H I J K) do call :scan_game_drive %%D
goto :eof

:check_game_dir
rem %~1 - candidate: Deadlock root, or ...\game, or ...\game\citadel
set "_g=%~1"
if not defined _g goto :eof
if defined GAME_DIR goto :eof
set "_g=%_g:"=%"
if not defined _g goto :eof
if "%_g:~-1%"=="\" set "_g=%_g:~0,-1%"
if exist "%_g%\game\citadel\gameinfo.gi" set "GAME_DIR=%_g%"
if defined GAME_DIR goto :eof
if exist "%_g%\citadel\gameinfo.gi" for %%i in ("%_g%\..") do set "GAME_DIR=%%~fi"
if defined GAME_DIR goto :eof
if exist "%_g%\gameinfo.gi" for %%i in ("%_g%\..\..") do set "GAME_DIR=%%~fi"
goto :eof

:scan_libraryfolders
set "_vdf=%~1"
if not exist "%_vdf%" goto :eof
for /f "usebackq tokens=1,2" %%A in ("%_vdf%") do call :lib_token "%%~A" "%%~B"
goto :eof

:lib_token
if defined GAME_DIR goto :eof
if /i not "%~1"=="path" goto :eof
set "_lib=%~2"
if not defined _lib goto :eof
set "_lib=%_lib:"=%"
set "_lib=%_lib:\\=\%"
if "%_lib:~-1%"=="\" set "_lib=%_lib:~0,-1%"
call :check_game_dir "%_lib%\steamapps\common\Deadlock"
goto :eof

:scan_game_drive
call :check_game_dir "%~1:\Steam\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
call :check_game_dir "%~1:\SteamLibrary\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
call :check_game_dir "%~1:\Games\Steam\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
call :check_game_dir "%~1:\Games\SteamLibrary\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
call :check_game_dir "%~1:\Program Files\Steam\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
call :check_game_dir "%~1:\Program Files (x86)\Steam\steamapps\common\Deadlock"
if defined GAME_DIR goto :eof
call :check_game_dir "%~1:\Deadlock"
goto :eof

:game_missing
echo.
echo [!] Deadlock was not found, and there is nowhere else to take the
echo     vanilla hud.xml from: the builder reads panorama/layout/hud.xml
echo     straight out of the game's .vpk packages and patches it.
echo.
echo     [1] Type the Deadlock folder by hand (you can drag it here)
echo     [2] Point at an already saved vanilla hud.xml
echo     [0] Quit
echo.
set "ANS="
set /p "ANS=    Choice: "
if "%ANS%"=="1" goto :game_manual_dir
if "%ANS%"=="2" goto :game_manual_hud
if "%ANS%"=="0" goto :fail
goto :game_missing

:game_manual_dir
echo.
set "ANS="
set /p "ANS=    Deadlock folder: "
set "ANS=%ANS:"=%"
if not defined ANS goto :game_missing
call :check_game_dir "%ANS%"
if defined GAME_DIR goto :game_found
echo.
echo     There is no game\citadel\gameinfo.gi there - not a Deadlock folder.
goto :game_missing

:game_manual_hud
echo.
set "ANS="
set /p "ANS=    Path to hud.xml: "
set "ANS=%ANS:"=%"
if not defined ANS goto :game_missing
if not exist "%ANS%" goto :hud_not_found
set "ARG_HUD=%ANS%"
echo [i] Vanilla hud.xml: %ARG_HUD%
echo [i] No game folder, so install later through Deadlock Mod Manager.
goto :game_found

:hud_not_found
echo.
echo     File not found: %ANS%
goto :game_missing

rem --- find CSDK -------------------------------------------------------------
:resolve_csdk
set "CSDK_DIR="
if defined ARG_CSDK call :check_csdk "%ARG_CSDK%"
if defined CSDK_DIR goto :eof
if defined DEADLOCK_CSDK call :check_csdk "%DEADLOCK_CSDK%"
if defined CSDK_DIR goto :eof
if defined CSDK_ROOT call :check_csdk "%CSDK_ROOT%"
if defined CSDK_DIR goto :eof
if defined CSDK_PATH call :check_csdk "%CSDK_PATH%"
if defined CSDK_DIR goto :eof
call :check_csdk "%MOD_DIR%\csdk"
if defined CSDK_DIR goto :eof
call :check_csdk "%USERPROFILE%\Deadlock SDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%USERPROFILE%\Desktop\Deadlock SDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%USERPROFILE%\Downloads\Deadlock SDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%USERPROFILE%\Documents\Deadlock SDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%USERPROFILE%\csdk"
if defined CSDK_DIR goto :eof
call :check_csdk "%USERPROFILE%\deadlock-csdk"
if defined CSDK_DIR goto :eof
for %%D in (C D E F G H I J K) do call :scan_csdk_drive %%D
goto :eof

:check_csdk
rem %~1 - candidate: CSDK root (contains game\bin_cs2\win64\resourcecompiler.exe)
set "_c=%~1"
if not defined _c goto :eof
if defined CSDK_DIR goto :eof
set "_c=%_c:"=%"
if not defined _c goto :eof
if "%_c:~-1%"=="\" set "_c=%_c:~0,-1%"
if exist "%_c%\game\bin_cs2\win64\resourcecompiler.exe" set "CSDK_DIR=%_c%"
if defined CSDK_DIR goto :eof
if exist "%_c%\game\bin\win64\resourcecompiler.exe" set "CSDK_DIR=%_c%"
if defined CSDK_DIR goto :eof
if exist "%_c%\bin\win64\resourcecompiler.exe" set "CSDK_DIR=%_c%"
goto :eof

:scan_csdk_drive
call :check_csdk "%~1:\Deadlock SDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%~1:\DeadlockSDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%~1:\csdk"
if defined CSDK_DIR goto :eof
call :check_csdk "%~1:\deadlock-csdk"
if defined CSDK_DIR goto :eof
call :check_csdk "%~1:\Games\Deadlock SDK"
if defined CSDK_DIR goto :eof
call :check_csdk "%~1:\Tools\Deadlock SDK"
goto :eof

rem --- misc ------------------------------------------------------------------
:no_sources
echo.
echo [!] There is no tools\build_mod.py next to this bat.
echo     Put build.bat into the bhop-hud-mod\ folder (or the repo root).
goto :fail

:usage
echo.
echo   Deadlock :: Bhop Trainer HUD - one-click mod builder
echo.
echo   build.bat [options]
echo.
echo     --game-path PATH     Deadlock folder (or its game\, or game\citadel)
echo     --csdk PATH          CSDK 12 folder with resourcecompiler.exe
echo     --csdk-install-dir PATH  where to unpack an auto-downloaded CSDK
echo     --csdk-url URL       your own CSDK archive link
echo     --no-csdk-download   never download: ask for the folder instead
echo     --csdk-redownload    ignore the cached archive and fetch it again
echo     --hud-xml FILE       an already saved vanilla panorama/layout/hud.xml
echo     --steps LIST         prepare,compile,pack,dmm,report (all by default)
echo     --out DIR            where to put artifacts (dist by default)
echo     --version X.Y.Z      mod version
echo     --allow-uncompiled   build without the compiler (will not work in game)
echo     --clean              delete build\ and dist\ first
echo     --test               run the builder's unit tests first
echo     -y                   ask nothing: install Python and CSDK itself (CI)
echo.
echo   Example:
echo     build.bat --game-path "D:\Steam\steamapps\common\Deadlock" --csdk "C:\Deadlock SDK"
echo     build.bat -y                        (everything unattended, from scratch)
echo     build.bat --clean -y --csdk-install-dir "C:\DeadlockModding"
echo.
goto :done

:fail
echo.
echo [X] Finished with errors. If the reason is unclear, look at
echo     %DIST_DIR%\BUILD_REPORT.md
echo     and at README.md in the mod folder.
echo.
if "%NONINTERACTIVE%"=="1" endlocal & exit /b 1
endlocal
pause
exit /b 1

:done
echo.
if "%NONINTERACTIVE%"=="1" endlocal & exit /b 0
endlocal
pause
exit /b 0
