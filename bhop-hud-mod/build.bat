@echo off
rem ===========================================================================
rem  Deadlock :: Bhop Trainer HUD  --  автоматическая сборка мода (Windows)
rem
rem  Запускается двойным кликом и делает всё само:
rem    * находит Python 3.9+ (если его нет - предлагает поставить через winget),
rem    * находит установленный Deadlock (реестр Steam + libraryfolders.vdf),
rem    * находит CSDK 12 c resourcecompiler.exe,
rem    * собирает VPK и пакет для Deadlock Mod Manager,
rem    * предлагает поставить мод в игру (через DMM или напрямую).
rem
rem  Ключи (все необязательны):
rem    build.bat --game-path "D:\Steam\steamapps\common\Deadlock"
rem               --csdk "C:\Deadlock SDK"
rem               --hud-xml "C:\hud.xml"      (если игры нет, а ванильный hud.xml есть)
rem               --version 1.2.0
rem               --steps prepare,compile,pack,dmm,report
rem               --out "D:\dist"
rem               --clean     (сначала снести build\ и dist\)
rem               --test      (перед сборкой прогнать тесты сборщика)
rem               -y          (не задавать вопросов, для CI)
rem  Неизвестные ключи передаются в tools\build_mod.py как есть.
rem ===========================================================================

setlocal
chcp 1251 >nul
title Deadlock :: Bhop Trainer HUD - сборка мода

rem --- где лежит мод: папка с этим батником, либо bhop-hud-mod рядом ----------
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
set "INSTALL_PY=%TOOLS_DIR%\install_dmm.py"
set "DIST_DIR=%MOD_DIR%\dist"
set "BUILD_DIR=%MOD_DIR%\build"

rem --- значения по умолчанию -------------------------------------------------
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
set "ALLOW_UNCOMPILED=0"
set "DO_CLEAN=0"
set "DO_TEST=0"
set "NONINTERACTIVE=0"
set "GAME_ARG="
set "DIST_ARG="
set "STEAM_PATH="
set "CSDK_URL=https://deadlockmodding.pages.dev/modding-tools/csdk-12"

rem --- разбор аргументов -----------------------------------------------------
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--game-path" set "ARG_GAME=%~2" & shift & shift & goto :parse_args
if /i "%~1"=="--csdk" set "ARG_CSDK=%~2" & shift & shift & goto :parse_args
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
echo   Deadlock :: Bhop Trainer HUD - автосборка
echo ============================================================
echo   Мод:  %MOD_DIR%
echo   Out:  %DIST_DIR%
echo.

if "%DO_CLEAN%"=="1" call :do_clean

rem --- Python ----------------------------------------------------------------
:python_search
call :find_python
if defined PY_CMD goto :python_found
if "%NONINTERACTIVE%"=="1" goto :python_missing
call :offer_python_install
call :find_python
if defined PY_CMD goto :python_found

:python_missing
echo.
echo [!] Python 3.9+ не найден - сборка невозможна.
echo.
echo     Варианты:
echo       1) winget install -e --id Python.Python.3.12
echo       2) https://www.python.org/downloads/  (галочка "Add python.exe to PATH")
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

:csdk_menu
echo.
echo [!] CSDK 12 c resourcecompiler.exe не найден.
echo     Без него движок Deadlock не грузит мод: нужны скомпилированные
echo     .vxml_c / .vcss_c, а не сырые .xml / .css.
echo.
echo     Взять CSDK: %CSDK_URL%
echo     (не распаковывай в OneDrive / Desktop / Documents - ломаются права)
echo.
if "%NONINTERACTIVE%"=="1" goto :csdk_missing_exit
echo     [1] Указать путь к CSDK вручную (можно перетащить папку в окно)
echo     [2] Открыть страницу загрузки CSDK
echo     [3] Собрать всё равно, только исходники (--allow-uncompiled,
echo         в игре такой мод не заработает, пакет DMM не собирается)
echo     [0] Выйти
echo.
set "ANS="
set /p "ANS=    Номер: "
if "%ANS%"=="1" goto :csdk_manual
if "%ANS%"=="2" goto :csdk_open_url
if "%ANS%"=="3" goto :csdk_allow_uncompiled
if "%ANS%"=="0" goto :fail
goto :csdk_menu

:csdk_manual
echo.
echo     Вставь путь к распакованному CSDK (внутри должна быть папка
echo     game\bin_cs2\win64\resourcecompiler.exe):
echo.
set "ANS="
set /p "ANS=    CSDK: "
set "ANS=%ANS:"=%"
if not defined ANS goto :csdk_menu
call :check_csdk "%ANS%"
if defined CSDK_DIR goto :csdk_found
echo.
echo     По этому пути resourcecompiler.exe не нашёлся.
goto :csdk_menu

:csdk_open_url
start "" "%CSDK_URL%"
goto :csdk_menu

:csdk_allow_uncompiled
set "ALLOW_UNCOMPILED=1"
echo.
echo [i] Собираю артефакты только с исходниками (в игре не работают).
goto :csdk_found

:csdk_missing_exit
echo [!] CSDK не найден, а ключ -y запрещает вопросы.
goto :fail

:csdk_found
if defined CSDK_DIR echo [i] CSDK:      %CSDK_DIR%
if /i not "%CSDK_DIR:OneDrive=%"=="%CSDK_DIR%" echo [!] Внимание: CSDK лежит в OneDrive - оттуда компилятор часто падает.

rem --- сборка ----------------------------------------------------------------
:do_build
echo.
echo ============================================================
echo   Сборка
echo ============================================================
echo.

set "BUILD_ARGS="
if defined GAME_DIR set "BUILD_ARGS=%BUILD_ARGS% --game-path "%GAME_DIR%""
if defined ARG_HUD set "BUILD_ARGS=%BUILD_ARGS% --hud-xml "%ARG_HUD%""
if defined CSDK_DIR set "BUILD_ARGS=%BUILD_ARGS% --csdk "%CSDK_DIR%""
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
echo [X] Сборка упала (код %BUILD_RC%).
echo     Подробности - в %DIST_DIR%\BUILD_REPORT.md
goto :fail

:build_cancel
echo.
echo [X] Сборку прервали.
goto :fail

:build_needs_compile
echo.
echo [!] Сборка остановилась на шаге компиляции (код 3).
echo     Проверь, что CSDK распакован и в нём есть
echo     game\bin_cs2\win64\resourcecompiler.exe
if "%NONINTERACTIVE%"=="1" goto :fail
echo.
set "ANS="
set /p "ANS=    Собрать только исходники (--allow-uncompiled)? [y/N]: "
if /i "%ANS%"=="y" set "ALLOW_UNCOMPILED=1"
if /i "%ANS%"=="y" goto :do_build
if /i "%ANS%"=="d" set "ALLOW_UNCOMPILED=1"
if /i "%ANS%"=="d" goto :do_build
goto :fail

:build_ok
echo.
echo ============================================================
echo   Готово
echo ============================================================
echo.
echo   Артефакты лежат в:
echo     %DIST_DIR%
echo.
echo     bhop_trainer_dir.vpk   - сам аддон
echo     dmm-bundle\            - пакет для Deadlock Mod Manager
echo     bhop_trainer-dmm.zip   - тот же пакет архивом
echo     BUILD_REPORT.md        - отчёт сборки
echo     SHA256SUMS.txt         - контрольные суммы
echo.
if "%ALLOW_UNCOMPILED%"=="1" echo [!] Это сборка без компилятора: в игре она не заработает.
if "%ALLOW_UNCOMPILED%"=="1" echo.
if "%NONINTERACTIVE%"=="1" goto :done

:menu
echo   Что дальше:
echo     [1] Открыть папку с пакетом DMM (импорт в Mod Manager)
echo     [2] Поставить мод в игру напрямую, мимо менеджера
echo     [3] Скопировать cfg тренажёра в game\citadel\cfg
echo     [4] Проверить установку
echo     [5] Открыть отчёт сборки
echo     [0] Выход
echo.
set "ANS="
set /p "ANS=    Номер: "
if "%ANS%"=="1" goto :act_stage
if "%ANS%"=="2" goto :act_direct
if "%ANS%"=="3" goto :act_cfg
if "%ANS%"=="4" goto :act_verify
if "%ANS%"=="5" goto :act_report
if "%ANS%"=="0" goto :done
echo     Не понял, попробуй ещё раз.
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
if not exist "%DIST_DIR%\BUILD_REPORT.md" echo     Отчёта нет: %DIST_DIR%\BUILD_REPORT.md
echo.
goto :menu


rem ==========================================================================
rem  Подпрограммы
rem ==========================================================================

rem --- поиск Python ----------------------------------------------------------
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
rem winget / установщик кладут Python сюда, даже если PATH не обновился
for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try_py_path "%%P\python.exe" ""
if defined PY_CMD goto :eof
if exist "%SystemDrive%\Python312\python.exe" call :try_py_path "%SystemDrive%\Python312\python.exe" ""
if defined PY_CMD goto :eof
if exist "%SystemDrive%\Python311\python.exe" call :try_py_path "%SystemDrive%\Python311\python.exe" ""
if defined PY_CMD goto :eof
if exist "%SystemDrive%\Python310\python.exe" call :try_py_path "%SystemDrive%\Python310\python.exe" ""
goto :eof

:try_py
rem %~1 - имя exe (py / python / python3), %~2 - доп. ключи ("-3" или "")
set "_py_name=%~1"
set "_py_extra=%~2"
set "_py_full="
for /f "delims=" %%i in ('where %_py_name% 2^>nul') do call :try_py_path "%%i" "%_py_extra%"
goto :eof

:try_py_path
rem %~1 - полный путь к python.exe, %~2 - доп. ключи
if defined PY_CMD goto :eof
set "_py_full=%~1"
if not defined _py_full goto :eof
if not exist "%_py_full%" goto :eof
rem python.exe из Microsoft Store - это заглушка, она не считается
if /i not "%_py_full:WindowsApps=%"=="%_py_full%" goto :eof
"%_py_full%" %~2 -c "import sys; sys.exit(0 if sys.version_info[0] == 3 and sys.version_info[1] >= 9 else 1)" >nul 2>nul
if errorlevel 1 goto :eof
rem без скобок: в пути может быть "Program Files (x86)"
if "%~2"=="" set "PY_CMD=%_py_full%"
if "%~2"=="" goto :eof
set "PY_CMD=%_py_full% %~2"
goto :eof

:offer_python_install
echo.
echo [!] Python 3.9+ не найден.
echo.
echo     [1] Поставить Python 3.12 самому через winget
echo     [2] Открыть страницу загрузки Python
echo     [0] Выйти
echo.
set "ANS="
set /p "ANS=    Номер: "
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
echo [i] Ставлю Python.Python.3.12 через winget...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :winget_failed
echo.
echo [i] Обновляю PATH в этом окне и ищу Python заново...
call :refresh_path
goto :eof

:no_winget
echo [!] winget не найден - поставь Python вручную:
echo     https://www.python.org/downloads/windows/
echo     (при установке отметь "Add python.exe to PATH")
echo.
echo     После установки закрой это окно и запусти батник заново.
goto :fail

:winget_failed
echo [!] winget не смог поставить Python (код %ERRORLEVEL%).
echo     Поставь вручную: https://www.python.org/downloads/windows/
goto :fail

:refresh_path
set "_sys_path="
set "_usr_path="
for /f "tokens=2,*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "_sys_path=%%B"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "_usr_path=%%B"
if defined _sys_path set "PATH=%_sys_path%;%PATH%"
if defined _usr_path set "PATH=%_usr_path%;%PATH%"
goto :eof

rem --- очистка ---------------------------------------------------------------
:do_clean
echo [i] Стираю рабочие каталоги...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
echo     готово.
echo.
goto :eof

rem --- тесты -----------------------------------------------------------------
:run_tests
echo [i] Прогоняю тесты сборщика...
%PY_CMD% -m unittest discover -s "%MOD_DIR%\tests" -t "%MOD_DIR%" -p "test_*.py"
if errorlevel 1 goto :tests_failed
echo     тесты пройдены.
echo.
goto :eof

:tests_failed
echo.
echo [X] Тесты упали - сборку останавливаю.
goto :fail

rem --- поиск Deadlock --------------------------------------------------------
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

rem Steam из реестра
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

rem типичные места по всем дискам
for %%D in (C D E F G H I J K) do call :scan_game_drive %%D
goto :eof

:check_game_dir
rem %~1 - кандидат: корень Deadlock, или ...\game, или ...\game\citadel
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
echo [!] Deadlock не найден, а ванильный hud.xml больше взять неоткуда:
echo     именно из паков игры сборщик достаёт panorama/layout/hud.xml,
echo     в который вшивается панель.
echo.
echo     [1] Указать папку Deadlock вручную (можно перетащить в окно)
echo     [2] Указать путь к уже сохранённому vanilla hud.xml
echo     [0] Выйти
echo.
set "ANS="
set /p "ANS=    Номер: "
if "%ANS%"=="1" goto :game_manual_dir
if "%ANS%"=="2" goto :game_manual_hud
if "%ANS%"=="0" goto :fail
goto :game_missing

:game_manual_dir
echo.
set "ANS="
set /p "ANS=    Папка Deadlock: "
set "ANS=%ANS:"=%"
if not defined ANS goto :game_missing
call :check_game_dir "%ANS%"
if defined GAME_DIR goto :game_found
echo.
echo     Там нет game\citadel\gameinfo.gi - это не папка Deadlock.
goto :game_missing

:game_manual_hud
echo.
set "ANS="
set /p "ANS=    Путь к hud.xml: "
set "ANS=%ANS:"=%"
if not defined ANS goto :game_missing
if not exist "%ANS%" goto :hud_not_found
set "ARG_HUD=%ANS%"
echo [i] Ванильный hud.xml: %ARG_HUD%
echo [i] Папки игры нет, поэтому установку потом делай через Deadlock Mod Manager.
goto :game_found

:hud_not_found
echo.
echo     Файл не найден: %ANS%
goto :game_missing

rem --- поиск CSDK ------------------------------------------------------------
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
rem %~1 - кандидат: корень CSDK (внутри game\bin_cs2\win64\resourcecompiler.exe)
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

rem --- прочее ----------------------------------------------------------------
:no_sources
echo.
echo [!] Рядом с батником нет tools\build_mod.py.
echo     Положи build.bat в папку bhop-hud-mod\ (или в корень репозитория).
goto :fail

:usage
echo.
echo   Deadlock :: Bhop Trainer HUD - автосборка мода
echo.
echo   build.bat [ключи]
echo.
echo     --game-path PATH     папка Deadlock (или её game\, или game\citadel)
echo     --csdk PATH          папка CSDK 12 c resourcecompiler.exe
echo     --hud-xml FILE       готовый ванильный panorama/layout/hud.xml
echo     --steps LIST         prepare,compile,pack,dmm,report (по умолчанию все)
echo     --out DIR            куда положить артефакты (по умолчанию dist)
echo     --version X.Y.Z      версия мода
echo     --allow-uncompiled   собрать без компилятора (в игре не заработает)
echo     --clean              сначала удалить build\ и dist\
echo     --test               перед сборкой прогнать тесты сборщика
echo     -y                   не задавать вопросов
echo.
echo   Пример:
echo     build.bat --game-path "D:\Steam\steamapps\common\Deadlock" --csdk "C:\Deadlock SDK"
echo.
goto :done

:fail
echo.
echo [X] Готово с ошибками. Если непонятно почему - смотри
echo     %DIST_DIR%\BUILD_REPORT.md
echo     и README.md в папке мода.
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
