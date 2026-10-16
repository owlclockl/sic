#!/usr/bin/env python3
"""Строгая заглушка ``resourcecompiler.exe``: повторяет поведение настоящего.

``fake_compiler.py`` проверяет раскладку входов/выходов, а эта — ещё и то, **как
сборщик вызывает компилятор**. Настоящий ``resourcecompiler``:

* определяет аддон по пути исходника внутри ``content/citadel_addons/<addon>`` и
  кладёт результат в ``game/citadel_addons/<addon>`` (штатный путь, флаг ``-game``
  для этого не нужен);
* если ``-game`` всё же передан — требует, чтобы по этому пути был ``gameinfo.gi``,
  иначе падает ещё до компиляции:

      Application unable to load gameinfo.gi file from directory "game\\citadel"
      Can't find 'game\\citadel\\gameinfo.gi'

  Именно на этом ломается Reduced CSDK 12: в нём есть ``bin*``/``content``/
  ``game/citadel_addons``, но нет файлов самой игры.

Переменная окружения ``SIC_FAKE_REQUIRE_GAME=1`` заставляет компилятор требовать
``-game`` даже без явного флага — так проверяется запасной вариант сборщика с
``gameinfo.gi`` из установленной игры. ``SIC_FAKE_COMPILER_LOG=<файл>`` пишет
журнал вызовов (argv + cwd), чтобы тесты сверяли команду дословно.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

COMPILED_EXTENSIONS = {
    ".xml": ".vxml_c",
    ".vxml": ".vxml_c",
    ".css": ".vcss_c",
    ".vcss": ".vcss_c",
    ".js": ".vjs_c",
    ".vjs": ".vjs_c",
    ".svg": ".vsvg_c",
    ".png": ".vtex_c",
    ".wav": ".vsnd_c",
}

#: Дословный текст ошибки настоящего компилятора (Reduced CSDK 12 без gameinfo.gi).
GAMEINFO_ERROR = (
    'Application unable to load gameinfo.gi file from directory "game\\citadel"\n'
    "Can't find 'game\\citadel\\gameinfo.gi'\n"
)


def addon_root_for(source: Path) -> Path | None:
    """``<CSDK>/content/citadel_addons/<addon>``, внутри которого лежит исходник."""
    source = source.resolve()
    for parent in source.parents:
        if parent.parent.name == "citadel_addons" and parent.parent.parent.name == "content":
            return parent
    return None


def output_root_for(addon_root: Path) -> Path:
    """Куда компилятор кладёт ``*_c``: соседний ``game/citadel_addons/<addon>``."""
    csdk = addon_root.parents[2]  # .../content/citadel_addons/<addon> -> корень CSDK
    return csdk / "game" / "citadel_addons" / addon_root.name


def _log_invocation() -> None:
    path = os.environ.get("SIC_FAKE_COMPILER_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": sys.argv, "cwd": os.getcwd()}) + "\n")
    except OSError:
        pass


def main() -> int:
    _log_invocation()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-i", dest="inputs", action="append", default=[])
    parser.add_argument("-game", dest="game", default=None)
    parser.add_argument("-nop4", action="store_true")
    parser.add_argument("-f", action="store_true")
    args, unknown = parser.parse_known_args()

    if args.game is not None:
        # Настоящий компилятор ищет gameinfo.gi по этому пути (каталог или файл).
        candidate = Path(args.game)
        gameinfo = candidate if candidate.is_file() else candidate / "gameinfo.gi"
        if not gameinfo.is_file():
            sys.stderr.write(GAMEINFO_ERROR)
            return 1
    elif os.environ.get("SIC_FAKE_REQUIRE_GAME") == "1":
        # Сборка CSDK, которая без -game работать отказывается.
        sys.stderr.write(GAMEINFO_ERROR)
        return 1

    sources = [Path(item) for item in args.inputs + [item for item in unknown if not item.startswith("-")]]
    if not sources:
        sys.stderr.write("no input files\n")
        return 1

    for source in sources:
        addon_root = addon_root_for(source)
        if addon_root is None:
            sys.stderr.write(f"input file is outside content/citadel_addons: {source}\n")
            return 1
        relative = source.resolve().relative_to(addon_root)
        extension = COMPILED_EXTENSIONS.get(relative.suffix.lower())
        if extension is None:
            print(f"strict resourcecompiler: {relative.as_posix()} — без компиляции")
            continue
        target = output_root_for(addon_root) / relative.with_suffix(extension)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = b"STRICT-COMPILED\n" + source.read_bytes()
        target.write_bytes(payload)
        print(f"strict resourcecompiler: {relative.as_posix()} -> {target.name} ({len(payload)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
