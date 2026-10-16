"""Общие помощники тестов: пути, синтетические паки, эталонные VPK."""

from __future__ import annotations

import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
MOD_ROOT = TESTS_DIR.parent
TOOLS_DIR = MOD_ROOT / "tools"
FIXTURES = TESTS_DIR / "fixtures"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import vanilla_hud

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


def craft_vpk(entries: list[tuple], data: bytes = b"", *, sections: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """Собрать VPK «руками» по спецификации — независимо от tools/vpk.py.

    ``entries`` — список ``(виртуальный путь, crc32, archive_index, offset, length)``
    или ``(…, length, preload_bytes)``: ``preload`` Valve пишет прямо в дерево,
    перед секцией данных. ``sections`` — размеры секций MD5/подписи: по формату
    они идут **после** данных (порядок: заголовок → дерево → данные → MD5 →
    подпись, см. ``Package.Save``/``Package.Read`` в ValvePak).
    """
    grouped: dict[tuple[str, str, str], list[tuple]] = {}
    for entry in entries:
        virtual_path, crc, archive_index, offset, length = entry[:5]
        preload = entry[5] if len(entry) > 5 else b""
        directory, _, name = virtual_path.rpartition("/")
        stem, _, extension = name.rpartition(".")
        grouped.setdefault((extension, directory or " ", stem), []).append(
            (stem, crc, archive_index, offset, length, preload)
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
                _stem, crc, archive_index, offset, length, preload = rows[0]
                tree += struct.pack("<IHHIIH", crc, len(preload), archive_index, offset, length, TERMINATOR)
                tree += preload
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"

    archive_md5, other_md5, signature = sections
    header = struct.pack("<7I", SIGNATURE, 2, len(tree), len(data), archive_md5, other_md5, signature)
    filler = b"\0" * (archive_md5 + other_md5 + signature)
    return bytes(header + tree + data + filler)


#: Что лежит в паке игры вместо исходника: движок грузит скомпилированный ресурс.
COMPILED_LAYOUT_FIXTURE = b"COMPILED-LAYOUT-FIXTURE"


def write_fake_game(
    root: Path,
    hud_xml_bytes: bytes | None = None,
    *,
    compiled_only: bool = False,
    build_id: str | None = None,
    extra_payloads: dict[str, bytes] | None = None,
) -> Path:
    """Фальшивая установка Deadlock с одним ``pak01_dir.vpk`` внутри.

    ``compiled_only`` повторяет настоящую игру: в паке нет исходного
    ``panorama/layout/hud.xml``, там лежит только скомпилированный
    ``panorama/layout/hud.vxml_c`` (проверено по содержимому
    ``game/citadel/pak01_dir.vpk`` самой игры).
    """
    citadel = root / "game" / "citadel"
    citadel.mkdir(parents=True, exist_ok=True)
    (citadel / "gameinfo.gi").write_text(GAMEINFO_SAMPLE, encoding="utf-8")
    if build_id:
        (citadel / "steam.inf").write_text(
            f"ClientVersion={build_id}\nServerVersion={build_id}\nProductName=citadel\n",
            encoding="utf-8",
        )
    from vpk import build_vpk

    payloads: dict[str, bytes] = {}
    if compiled_only:
        payloads["panorama/layout/hud.vxml_c"] = COMPILED_LAYOUT_FIXTURE
        payloads["panorama/styles/hud.vcss_c"] = b"COMPILED-STYLES-FIXTURE"
    elif hud_xml_bytes is not None:
        payloads["panorama/layout/hud.xml"] = hud_xml_bytes
    if extra_payloads:
        payloads.update(extra_payloads)
    (citadel / "pak01_dir.vpk").write_bytes(build_vpk(payloads or {"panorama/layout/hud.xml": b"<root />"}))
    (citadel / "addons").mkdir(exist_ok=True)
    return root


def write_loose_hud(game_dir: Path, hud_xml_bytes: bytes) -> Path:
    """Положить ванильный ``hud.xml`` файлом в дерево игры (как выгрузку S2V)."""
    target = Path(game_dir) / "game" / "citadel" / "panorama" / "layout" / "hud.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(hud_xml_bytes)
    return target


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


_real_fetch_bytes = vanilla_hud.fetch_bytes


def offline_fetch(url: str, timeout: int = vanilla_hud.DEFAULT_TIMEOUT) -> bytes:
    """Заглушка сети для тестов: ``file://`` можно, настоящие запросы — нет.

    Тесты не должны зависеть от доступности GitHub/Google Диска: любые ссылки
    подставляются через ``fetch`` или ``file://``. Если код вдруг полез в сеть
    сам — тест падает, а не висит на таймауте.
    """
    if url.startswith("file:"):
        return _real_fetch_bytes(url)
    raise vanilla_hud.HudSourceError(f"тест: сеть запрещена ({url})")


def block_network(case: unittest.TestCase) -> None:
    """На время теста запретить настоящие http-запросы."""
    patcher = mock.patch.object(vanilla_hud, "fetch_bytes", offline_fetch)
    patcher.start()
    case.addCleanup(patcher.stop)
