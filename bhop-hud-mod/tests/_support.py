"""Общие помощники тестов: пути, синтетические паки, эталонные VPK."""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
MOD_ROOT = TESTS_DIR.parent
TOOLS_DIR = MOD_ROOT / "tools"
FIXTURES = TESTS_DIR / "fixtures"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

PYTHON = sys.executable
FAKE_COMPILER = FIXTURES / "fake_compiler.py"
HUD_FIXTURE = FIXTURES / "hud_min.xml"

COMPILER_CMD_TEMPLATE = (
    f'"{PYTHON}" "{FAKE_COMPILER}" --source {{source}} '
    "--content-root {content_root} --output-root {output_root} -game {game}"
)

SIGNATURE = 0x55AA1234
INLINE = 0x7FFF
TERMINATOR = 0xFFFF


def _cstring(value: str) -> bytes:
    return value.encode("utf-8") + b"\0"


def craft_vpk(entries: list[tuple[str, int, int, int, int]], data: bytes = b"") -> bytes:
    """Собрать VPK «руками» по спецификации — независимо от tools/vpk.py.

    ``entries`` — список ``(виртуальный путь, crc32, archive_index, offset, length)``.
    Нужен, чтобы проверять читатель на структурах, которые наш writer не делает
    (например, многокомпонентные паки с companion-архивами).
    """
    grouped: dict[tuple[str, str, str], list[tuple[str, int, int, int, int]]] = {}
    for virtual_path, crc, archive_index, offset, length in entries:
        directory, _, name = virtual_path.rpartition("/")
        stem, _, extension = name.rpartition(".")
        grouped.setdefault((extension, directory or " ", stem), []).append(
            (stem, crc, archive_index, offset, length)
        )

    tree = bytearray()
    for extension in sorted({key[0] for key in grouped}):
        tree += _cstring(extension)
        for directory in sorted({key[1] for key in grouped if key[0] == extension}):
            tree += _cstring(directory)
            for (ext, dir_, stem), rows in sorted(grouped.items()):
                if ext != extension or dir_ != directory:
                    continue
                tree += _cstring(stem)
                _stem, crc, archive_index, offset, length = rows[0]
                tree += struct.pack("<IHHIIH", crc, 0, archive_index, offset, length, TERMINATOR)
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"

    header = struct.pack("<7I", SIGNATURE, 2, len(tree), len(data), 0, 0, 0)
    return bytes(header + tree + data)


def write_fake_game(root: Path, hud_xml_bytes: bytes) -> Path:
    """Фальшивая установка Deadlock с одним ``pak01_dir.vpk`` внутри."""
    citadel = root / "game" / "citadel"
    citadel.mkdir(parents=True, exist_ok=True)
    (citadel / "gameinfo.gi").write_text(GAMEINFO_SAMPLE, encoding="utf-8")
    from vpk import build_vpk

    pak = build_vpk({"panorama/layout/hud.xml": hud_xml_bytes})
    (citadel / "pak01_dir.vpk").write_bytes(pak)
    (citadel / "addons").mkdir(exist_ok=True)
    return root


GAMEINFO_SAMPLE = """// synthetic test fixture: упрощённая копия структуры ванильного gameinfo.gi
"GameInfo"
{
	game        "Citadel"
	title       "Deadlock"

	FileSystem
	{
		SearchPaths
		{
			// These are optional language paths. They must be mounted first, which is why there are first in the list.
			// *LANGUAGE* will be replaced with the actual language name.
			Game_UILanguage		citadel_*LANGUAGE*

			// These are optional low-violence paths. They will only get mounted if you're in a low-violence mode.
			Game_LowViolence	citadel_lv

			Game                citadel
			Game                core
		}
	}
	AddonConfig
	{
		"UseOfficialAddons" "1"
	}
}
"""


def run_cli(module_path: Path, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Запустить CLI отдельным процессом (проверка кодов возврата)."""
    return subprocess.run(
        [PYTHON, str(module_path), *args],
        cwd=str(cwd or MOD_ROOT), capture_output=True, text=True,
    )
