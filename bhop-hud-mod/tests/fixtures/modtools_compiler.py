#!/usr/bin/env python3
"""Заглушка ``resourcecompiler``, падающего **после** компиляции ресурса.

Повторяет то, что случилось в трекере: ``resourcecompiler.exe`` из Reduced CSDK 12
читает дерево установленной игры (вариант ``-game <Deadlock>/game/citadel``), грузит
материалы, печатает предупреждения про шейдеры, пишет ``*_c`` и **на выходе** не может
загрузить собственный модуль инструментов:

    CAppSystemDict:Unable to load module modtools (Dependency of application), error 126

Код возврата при этом 1. Сборщик обязан отличить это от настоящей ошибки компиляции:
свежий ресурс уже на месте, значит шаг выполнен (с предупреждением), а не «CSDK сломан,
перекачивай гигабайты».

* ``SIC_FAKE_NO_OUTPUT=1`` — ресурс не записывается (настоящая ошибка компиляции);
* ``SIC_FAKE_COMPILER_LOG=<файл>`` — журнал вызовов, как у ``strict_compiler.py``.
"""

from __future__ import annotations

import argparse
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

#: Дословные строки из вывода настоящего компилятора (лог из отчёта сборки).
NOISE = (
    "Creating device for graphics adapter 0 'NVIDIA GeForce RTX 4060 Ti' [vendorid 0x10DE]: 32.0.16.1742",
    'VCS file format is "?"WARNING: File might be corrupt or missing - '
    "'vpk:c:\\Program Files (x86)\\Steam\\steamapps\\common\\Deadlock\\game\\citadel\\"
    'shaders_pc.vpk:shaders\\vfx\\error_pc_50_features.vcs\'',
    "CMaterial2::LoadShadersAndSetupModes(1614): Error creating shader error.vfx for material "
    "materials/error.vmat!",
    "Unable to find fgd file models_gamedata.fgd!",
    "WARNING: Unable to load the FGD file 'models_gamedata.fgd'.",
    "CAppSystemDict:Unable to load module modtools (Dependency of application), error 126",
)


def addon_root_for(source: Path) -> Path | None:
    source = source.resolve()
    for parent in source.parents:
        if parent.parent.name == "citadel_addons" and parent.parent.parent.name == "content":
            return parent
    return None


def main() -> int:
    path = os.environ.get("SIC_FAKE_COMPILER_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(" ".join(sys.argv) + "\n")
        except OSError:
            pass

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-i", dest="inputs", action="append", default=[])
    parser.add_argument("-game", dest="game", default=None)
    parser.add_argument("-nop4", action="store_true")
    parser.add_argument("-f", action="store_true")
    args, unknown = parser.parse_known_args()

    if os.environ.get("SIC_FAKE_NO_OUTPUT") != "1":
        for item in args.inputs + [value for value in unknown if not value.startswith("-")]:
            source = Path(item)
            addon_root = addon_root_for(source)
            if addon_root is None:
                continue
            relative = source.resolve().relative_to(addon_root)
            extension = COMPILED_EXTENSIONS.get(relative.suffix.lower())
            if extension is None:
                continue
            csdk = addon_root.parents[2]
            target = csdk / "game" / "citadel_addons" / addon_root.name / relative.with_suffix(extension)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"MODTOOLS-COMPILED\n" + source.read_bytes())
            print(f"resourcecompiler: {relative.as_posix()} -> {target.name}")

    for line in NOISE:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
