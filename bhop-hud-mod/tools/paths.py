"""Поиск установленных путей: Deadlock, CSDK, данные Deadlock Mod Manager.

Всё — только стандартная библиотека и без обращения к реестру: перебираем
типичные места Steam, читаем ``libraryfolders.vdf`` (простым регэкспом, без
полноценного парсера KeyValues) и проверяем, что найденное похоже на игру
(``game/citadel/gameinfo.gi``).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GAME_DIR_NAME = "Deadlock"
GAMEINFO_RELATIVE = Path("game") / "citadel" / "gameinfo.gi"
DMM_APP_ID = "dev.stormix.deadlock-mod-manager"
DMM_SUPPORTED_TIP = "https://deadlockmods.app/"


def _is_game_dir(path: Path) -> bool:
    return (Path(path) / GAMEINFO_RELATIVE).is_file()


def normalize_game_dir(path: Path) -> Path | None:
    """Принять либо корень Deadlock, либо ``.../game``, либо ``.../game/citadel``.

    Подъём на уровень выше делается только для ожидаемых имён (``game``,
    ``citadel``): случайный путь вроде ``<Deadlock>/nope`` не должен молча
    считаться корнем игры.
    """
    path = Path(path)
    if _is_game_dir(path):
        return path
    if path.name == "game" and _is_game_dir(path.parent):
        return path.parent
    if path.name == "citadel" and _is_game_dir(path.parent.parent):
        return path.parent.parent
    return None


def _steam_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    if sys.platform.startswith("win"):
        program_files = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")]
        for base in program_files:
            if base:
                roots.append(Path(base) / "Steam")
        roots += [Path("C:/Steam"), Path("D:/Steam"), Path("E:/Steam")]
    elif sys.platform == "darwin":
        roots.append(home / "Library" / "Application Support" / "Steam")
    else:
        roots += [
            home / ".steam" / "steam",
            home / ".steam" / "root",
            home / ".local" / "share" / "Steam",
            home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
            home / ".flatpak" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        ]
    return [root for root in roots if root.is_dir()]


def _library_roots(steam_root: Path) -> list[Path]:
    """Каталоги библиотек Steam: сам steam + всё из ``libraryfolders.vdf``."""
    libraries = [steam_root]
    for vdf in (steam_root / "steamapps" / "libraryfolders.vdf", steam_root / "config" / "libraryfolders.vdf"):
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s*"([^"]+)"', text):
            candidate = Path(match.group(1).replace("\\\\", "\\"))
            if candidate.is_dir():
                libraries.append(candidate)
    unique: list[Path] = []
    for library in libraries:
        if library not in unique:
            unique.append(library)
    return unique


def find_game_dir(explicit: Path | None = None) -> Path | None:
    """Найти установленный Deadlock. ``explicit`` проверяется первым."""
    if explicit:
        found = normalize_game_dir(explicit)
        if found is None:
            raise FileNotFoundError(f"по пути {explicit} нет Deadlock (ожидался game/citadel/gameinfo.gi)")
        return found

    for variable in ("DEADLOCK_DIR", "DEADLOCK_GAME_DIR", "DEADLOCK_PATH"):
        value = os.environ.get(variable)
        if value:
            found = normalize_game_dir(Path(value))
            if found:
                return found

    for steam_root in _steam_roots():
        for library in _library_roots(steam_root):
            candidate = library / "steamapps" / "common" / GAME_DIR_NAME
            if _is_game_dir(candidate):
                return candidate
    return None


def _auto_install_root() -> Path:
    """Каталог, в который ``tools/csdk_setup.py`` ставит CSDK по умолчанию.

    Дублировать логику не хочется, но импортировать csdk_setup из paths нельзя
    (он сам импортирует paths — получится кольцо), поэтому повторяем только
    выбор каталога.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "DeadlockModding" / "Reduced_CSDK_12"
        if not any(part in str(candidate).lower() for part in ("onedrive", "desktop", "documents")):
            return candidate
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        return Path(system_drive + "\\") / "DeadlockModding" / "Reduced_CSDK_12"
    return Path.home() / ".local" / "share" / "deadlock-csdk" / "Reduced_CSDK_12"


def find_csdk(explicit: Path | None = None) -> Path | None:
    """Найти CSDK с рабочим ``resourcecompiler``.

    Поддерживается «Reduced CSDK 12» и полный CSDK: компилятор ищется во всех
    известных раскладках (см. ``COMPILER_RELATIVE_PATHS``).
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for variable in ("DEADLOCK_CSDK", "CSDK_ROOT", "CSDK_PATH"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value))
    candidates += [
        Path.cwd() / "csdk",
        Path.home() / "Deadlock SDK",
        Path.home() / "csdk",
        Path("C:/Deadlock SDK"),
        Path("C:/csdk"),
        Path("D:/Deadlock SDK"),
        # куда tools/csdk_setup.py ставит CSDK при автоустановке
        _auto_install_root(),
        Path("/opt/csdk"),
        Path("/opt/deadlock-csdk"),
    ]
    for candidate in candidates:
        resolved = csdk_compiler(candidate)
        if resolved is not None:
            return candidate
    return None


#: Где в CSDK может лежать ``resourcecompiler`` — раскладки у сборок разные.
#:
#: Порядок — от исторических к нынешним. В старых CSDK 12 (и в полном CSDK от
#: Valve) бинарники лежат под ``game/``; в текущем Reduced CSDK 12 наборы
#: бинарников (``bin``, ``bin_cs2``, ``bin_server``, ``bin_tools``) вынесены в
#: корень архива, а в ``game/`` остаётся только ``citadel_addons`` — файлов самой
#: игры там нет. Живые примеры сообщества: ``game/bin_cs2/win64`` (QOLLOCK,
#: Deadlock QoL HUD Mod), ``game/bin_tools/win64`` (DeadPacker, Deadlock Stats
#: Editor), ``game/bin_server/win64`` (Hiokiii), корневой ``bin/win64`` —
#: установка Reduced CSDK 12 после распаковки ``tools/csdk_setup.py``.
COMPILER_RELATIVE_PATHS: tuple[Path, ...] = tuple(
    Path(directory) / "win64" / name
    for directory in (
        "game/bin_cs2",
        "game/bin",
        "game/bin_tools",
        "game/bin_server",
        "bin_cs2",
        "bin",
        "bin_tools",
        "bin_server",
    )
    for name in ("resourcecompiler.exe", "resourcecompiler")
)


def _looks_like_csdk_root(path: Path) -> bool:
    """Похож ли каталог на корень CSDK (проект Source 2).

    Нужно для раскладок с бинарниками в корне (``bin/win64``): без этой проверки
    корнем «сойдёт» и подкаталог старой раскладки — ``<CSDK>/game``, ведь в нём
    лежит ``bin_cs2/win64/resourcecompiler.exe``. У настоящего корня CSDK рядом с
    бинарниками обязательно есть ``content/`` (а у старой раскладки — ``game/``).
    """
    return (path / "content").is_dir() or (path / "game").is_dir()


def csdk_compiler(csdk_root: Path) -> Path | None:
    """Путь к ``resourcecompiler`` внутри CSDK, если он там есть."""
    csdk_root = Path(csdk_root)
    for relative in COMPILER_RELATIVE_PATHS:
        candidate = csdk_root / relative
        if not candidate.is_file():
            continue
        if relative.parts[0].startswith("bin") and not _looks_like_csdk_root(csdk_root):
            # Бинарники в корне, но каталог не тянет на корень CSDK — это
            # подкаталог (например ``<CSDK>/game``), а не сам CSDK.
            continue
        return candidate
    return None


def csdk_game_dir(csdk_root: Path) -> Path:
    """Каталог «базового мода» внутри CSDK: ``<CSDK>/game/citadel``.

    ``resourcecompiler`` берёт отсюда ``gameinfo.gi``, когда его просят явно
    (``-game``). В Reduced CSDK 12 этого файла обычно нет — и это не мешает
    компиляции: аддон определяется по пути исходника внутри
    ``content/citadel_addons/<addon>``, а ``-game`` такому компилятору передавать
    нельзя (он падает с «unable to load gameinfo.gi file from directory»).
    """
    return Path(csdk_root) / "game" / "citadel"


def csdk_gameinfo(csdk_root: Path) -> Path:
    """``<CSDK>/game/citadel/gameinfo.gi`` — есть не во всех сборках CSDK."""
    return csdk_game_dir(csdk_root) / "gameinfo.gi"


def find_dmm_data_dir() -> Path | None:
    """Каталог данных Deadlock Mod Manager (только для отчёта ``check``)."""
    home = Path.home()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        candidates = [Path(base) / DMM_APP_ID] if base else []
        candidates.append(home / "AppData" / "Roaming" / DMM_APP_ID)
    elif sys.platform == "darwin":
        candidates = [home / "Library" / "Application Support" / DMM_APP_ID]
    else:
        candidates = [
            home / ".local" / "share" / DMM_APP_ID,
            home / ".config" / DMM_APP_ID,
        ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


@dataclass(frozen=True)
class EnvReport:
    game_dir: Path | None
    game_dir_source: str
    csdk: Path | None
    compiler: Path | None
    dmm_data_dir: Path | None
    game_running: bool | None
    platform: str

    def lines(self) -> list[str]:
        def mark(value: bool) -> str:
            return "да" if value else "нет"

        out = [f"Платформа: {self.platform}"]
        if self.game_dir:
            out.append(f"Deadlock: {self.game_dir} ({self.game_dir_source})")
        else:
            out.append("Deadlock: не найден (укажи --game-path)")
        out.append(f"CSDK / resourcecompiler: {self.compiler if self.compiler else 'не найден'}")
        if self.csdk:
            out.append(f"CSDK root: {self.csdk}")
        out.append(f"Данные Deadlock Mod Manager: {self.dmm_data_dir if self.dmm_data_dir else 'не найдены'}")
        if self.game_running is None:
            out.append("Процесс citadel.exe: неизвестно")
        else:
            out.append(f"Игра запущена: {mark(self.game_running)}")
        return out


def env_report(explicit_game_dir: Path | None = None, explicit_csdk: Path | None = None) -> EnvReport:
    source = "аргумент --game-path" if explicit_game_dir else "автоопределение"
    game_dir = find_game_dir(explicit_game_dir)
    if game_dir is None:
        source = "не найден"
    csdk = find_csdk(explicit_csdk)
    return EnvReport(
        game_dir=game_dir,
        game_dir_source=source,
        csdk=csdk,
        compiler=csdk_compiler(csdk) if csdk else None,
        dmm_data_dir=find_dmm_data_dir(),
        game_running=is_game_running(),
        platform=sys.platform,
    )


#: Имена процесса игры, по которым её узнаём (Wine/Proton тоже даёт ``citadel.exe``).
GAME_PROCESS_NAMES = ("citadel.exe", "citadel")


def _process_looks_like_game(proc_dir: Path) -> bool:
    """Похож ли процесс на Deadlock: сверяем имя процесса и argv[0], а не всю строку.

    Искать подстроку ``citadel`` во всей командной строке нельзя: путь игры сам
    содержит ``game/citadel``, поэтому установщик, запущенный с ``--game-path
    .../Deadlock/game/citadel``, «видел» бы запущенную игру в самом себе.
    """
    try:
        comm = (proc_dir / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError:
        comm = ""
    if comm in GAME_PROCESS_NAMES:
        return True
    try:
        raw = (proc_dir / "cmdline").read_bytes()
    except OSError:
        return False
    if not raw:
        return False
    argv0 = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
    return Path(argv0).name.lower() in GAME_PROCESS_NAMES


def is_game_running(proc_root: Path | None = None) -> bool | None:
    """Запущен ли ``citadel.exe``. ``None`` — определить не удалось.

    ``proc_root`` нужен только тестам: подменяет каталог ``/proc``.
    """
    try:
        if sys.platform.startswith("win"):
            output = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq citadel.exe", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout.lower()
            return "citadel.exe" in output
        if sys.platform == "darwin":
            output = subprocess.run(["pgrep", "-x", "citadel"], capture_output=True, text=True, timeout=10)
            return output.returncode == 0
        proc = proc_root if proc_root is not None else Path("/proc")
        if proc.is_dir():
            for entry in proc.iterdir():
                if entry.name.isdigit() and _process_looks_like_game(entry):
                    return True
            return False
    except (OSError, subprocess.SubprocessError):
        return None
    return None
