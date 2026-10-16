"""Тесты VPK-контейнера: запись, чтение, проверка, извлечение из паков игры."""

from __future__ import annotations

import mmap
import struct
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import INLINE, TERMINATOR, craft_vpk

import vpk


def _dmm_style_reader(data: bytes) -> list[dict[str, object]]:
    """Независимый разбор дерева — повторяет логику парсера Deadlock Mod Manager.

    Нужен как «второе мнение»: если наш writer и наш reader договорились между
    собой, но оба разошлись со спекой, этот разбор это поймает.
    """
    signature, version, tree_length = struct.unpack_from("<3I", data, 0)
    assert signature == 0x55AA1234, "сигнатура"
    header_size = 28 if version >= 2 else 12
    tree_end = header_size + tree_length
    cursor = header_size
    entries: list[dict[str, object]] = []

    def cstring() -> str:
        nonlocal cursor
        end = data.index(b"\0", cursor, tree_end)
        value = data[cursor:end].decode("utf-8")
        cursor = end + 1
        return value

    while cursor < tree_end:
        extension = cstring()
        if not extension:
            break
        while cursor < tree_end:
            directory = cstring()
            if not directory:
                break
            while cursor < tree_end:
                stem = cstring()
                if not stem:
                    break
                crc, preload, archive_index, offset, length, terminator = struct.unpack_from("<IHHIIH", data, cursor)
                cursor += 18
                assert terminator == TERMINATOR, "терминатор записи"
                name = f"{stem}.{extension}"
                full = f"{directory}/{name}" if directory != " " else name
                entries.append(
                    {
                        "path": full.lower(),
                        "crc": crc,
                        "archive_index": archive_index,
                        "offset": offset,
                        "length": length,
                        "preload": preload,
                    }
                )
    return entries


class BuildVpkTest(unittest.TestCase):
    def test_round_trip_preserves_paths_and_bytes(self) -> None:
        payloads = {
            "panorama/layout/hud.vxml_c": b"layout-bytes",
            "panorama/styles/bhop_trainer.vcss_c": b"css-bytes" * 3,
            "addoninfo.txt": b"info",
            "modinfo.json": b"{}",
        }
        data = vpk.build_vpk(payloads)
        archive = vpk.VpkArchive(Path("<mem>"), data)
        self.assertEqual(sorted(payloads), sorted(archive.list_entries()))
        for name, payload in payloads.items():
            self.assertEqual(payload, archive.extract(name))
        self.assertEqual([], vpk.verify_vpk(data, payloads))

    def test_byte_layout_matches_spec(self) -> None:
        payloads = {"panorama/styles/x.vcss": b"AA", "addoninfo.txt": b"B"}
        data = vpk.build_vpk(payloads)

        crc_one, crc_two = zlib.crc32(b"B") & 0xFFFFFFFF, zlib.crc32(b"AA") & 0xFFFFFFFF
        record_one = struct.pack("<IHHIIH", crc_one, 0, INLINE, 0, 1, TERMINATOR)
        record_two = struct.pack("<IHHIIH", crc_two, 0, INLINE, 1, 2, TERMINATOR)
        tree = (
            b"txt\0" b" \0" b"addoninfo\0" + record_one + b"\0\0"
            b"vcss\0" b"panorama/styles\0" b"x\0" + record_two + b"\0\0"
            b"\0"
        )
        expected = struct.pack("<7I", 0x55AA1234, 2, len(tree), 3, 0, 0, 0) + tree + b"BAA"
        self.assertEqual(expected, data)

        entries = _dmm_style_reader(data)
        self.assertEqual(
            [
                {"path": "addoninfo.txt", "crc": crc_one, "archive_index": INLINE,
                 "offset": 0, "length": 1, "preload": 0},
                {"path": "panorama/styles/x.vcss", "crc": crc_two, "archive_index": INLINE,
                 "offset": 1, "length": 2, "preload": 0},
            ],
            entries,
        )

    def test_paths_are_lowercased_and_slash_normalised(self) -> None:
        data = vpk.build_vpk({"Panorama\\Styles\\Foo.VCSS": b"x"})
        archive = vpk.VpkArchive(Path("<mem>"), data)
        self.assertEqual(["panorama/styles/foo.vcss"], archive.list_entries())

    def test_rejects_files_without_extension(self) -> None:
        with self.assertRaises(vpk.VpkError):
            vpk.build_vpk({"panorama/styles/readme": b"x"})

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(vpk.VpkError):
            vpk.build_vpk({})

    def test_verify_detects_corrupted_payload(self) -> None:
        payloads = {"panorama/styles/x.vcss": b"payload"}
        data = bytearray(vpk.build_vpk(payloads))
        data[-1] ^= 0xFF  # портим последний байт данных
        problems = vpk.verify_vpk(bytes(data), payloads)
        self.assertTrue(any("содержимое" in problem or "CRC32" in problem for problem in problems), problems)

    def test_verify_reports_missing_and_extra_entries(self) -> None:
        data = vpk.build_vpk({"a/b.vcss": b"1"})
        problems = vpk.verify_vpk(data, {"a/b.vcss": b"1", "a/c.vcss": b"2"})
        self.assertTrue(any("нет записи a/c.vcss" in problem for problem in problems), problems)


class ReaderTest(unittest.TestCase):
    def test_reads_companion_archive(self) -> None:
        """Многокомпонентный пак: данные лежат в pak01_000.vpk."""
        payload = b"companion-bytes"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory_vpk = root / "pak01_dir.vpk"
            directory_vpk.write_bytes(
                craft_vpk([("panorama/layout/hud.xml", zlib.crc32(payload) & 0xFFFFFFFF, 0, 0, len(payload))])
            )
            (root / "pak01_000.vpk").write_bytes(payload)

            archive = vpk.open_vpk(directory_vpk)
            entry = archive.find("panorama/layout/hud.xml")
            self.assertIsNotNone(entry)
            self.assertFalse(entry.is_inline)
            self.assertEqual(archive.companion_path(0), root / "pak01_000.vpk")
            self.assertEqual(payload, archive.extract("panorama/layout/hud.xml"))

    def test_missing_companion_reports_clearly(self) -> None:
        with TemporaryDirectory() as tmp:
            directory_vpk = Path(tmp) / "pak01_dir.vpk"
            directory_vpk.write_bytes(craft_vpk([("a/b.vcss", 0, 1, 0, 4)]))
            with self.assertRaises(vpk.VpkError) as ctx:
                vpk.open_vpk(directory_vpk).extract("a/b.vcss")
            self.assertIn("pak01_001.vpk", str(ctx.exception))

    def test_extract_all_writes_tree_and_blocks_escape(self) -> None:
        data = craft_vpk([("../escape.vcss", 0, 0x7FFF, 0, 1), ("ok/file.vcss", 0, 0x7FFF, 1, 1)], b"XY")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vpk_path = root / "pak09_dir.vpk"
            vpk_path.write_bytes(data)
            written = vpk.open_vpk(vpk_path).extract_all(root / "out")
            self.assertEqual(["ok/file.vcss"], written)
            self.assertEqual(b"Y", (root / "out" / "ok" / "file.vcss").read_bytes())
            self.assertFalse((root / "escape.vcss").exists())

    def test_rejects_non_vpk(self) -> None:
        with self.assertRaises(vpk.VpkError):
            vpk.VpkArchive(Path("<mem>"), b"not a vpk at all")

    def test_extract_from_game_prefers_lowest_pak(self) -> None:
        citadel = Path(self._tmpdir()) / "citadel"
        citadel.mkdir(parents=True)
        (citadel / "pak01_dir.vpk").write_bytes(vpk.build_vpk({"panorama/layout/hud.xml": b"vanilla"}))
        (citadel / "pak02_dir.vpk").write_bytes(vpk.build_vpk({"panorama/layout/hud.xml": b"patched by someone"}))
        self.assertEqual(b"vanilla", vpk.extract_from_game(citadel, "panorama/layout/hud.xml"))
        self.assertIsNone(vpk.extract_from_game(citadel, "panorama/layout/missing.xml"))

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _tmpdir(self) -> str:
        return self._tmp.name


class HashTest(unittest.TestCase):
    def test_crc32_matches_iso_hdlc(self) -> None:
        self.assertEqual(zlib.crc32(b"123456789") & 0xFFFFFFFF, vpk.crc32_of(b"123456789"))

    def test_sha256_of_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                vpk.sha256_of_file(path),
            )


class PreloadAndSectionTest(unittest.TestCase):
    """Тонкости настоящих паков Valve: preload-хвост в дереве и секции за данными."""

    def test_preload_bytes_are_part_of_the_file(self) -> None:
        preload = b"preloaded-head"
        tail = b"archive-tail"
        payload = preload + tail
        data = craft_vpk(
            [("panorama/layout/hud.xml", zlib.crc32(payload) & 0xFFFFFFFF, INLINE, 0, len(tail), preload)],
            tail,
        )
        archive = vpk.VpkArchive(Path("<mem>"), data)
        entry = archive.find("panorama/layout/hud.xml")
        self.assertIsNotNone(entry)
        self.assertEqual(len(preload), entry.preload_bytes)
        self.assertEqual(len(payload), entry.total_length)
        self.assertEqual(payload, archive.read_entry(entry, verify_crc=True))

    def test_sections_after_data_do_not_shift_offsets(self) -> None:
        # Секции MD5/подписи идут ПОСЛЕ секции данных, поэтому offset'ы не меняют.
        payload = b"layout-bytes"
        data = craft_vpk(
            [("panorama/layout/hud.xml", zlib.crc32(payload) & 0xFFFFFFFF, INLINE, 0, len(payload))],
            payload,
            sections=(28, 48, 20),
        )
        archive = vpk.VpkArchive(Path("<mem>"), data)
        self.assertEqual(payload, archive.extract("panorama/layout/hud.xml"))

    def test_crc_mismatch_is_detected_on_request(self) -> None:
        data = craft_vpk([("a/b.vcss", 0xDEADBEEF, INLINE, 0, 2)], b"XY")
        archive = vpk.VpkArchive(Path("<mem>"), data)
        entry = archive.find("a/b.vcss")
        self.assertIsNotNone(entry)
        # без проверки данные отдаются как есть, с проверкой — понятная ошибка
        self.assertEqual(b"XY", archive.read_entry(entry))
        with self.assertRaises(vpk.VpkError) as ctx:
            archive.read_entry(entry, verify_crc=True)
        self.assertIn("CRC32", str(ctx.exception))

    def test_crc_mismatch_breaks_reading_of_truncated_entry(self) -> None:
        data = craft_vpk([("a/b.vcss", zlib.crc32(b"XY") & 0xFFFFFFFF, INLINE, 0, 2)], b"X")
        archive = vpk.VpkArchive(Path("<mem>"), data)
        with self.assertRaises(vpk.VpkError):
            archive.read_entry(archive.find("a/b.vcss"), verify_crc=True)


class LargePakTest(unittest.TestCase):
    def test_large_pak_is_memory_mapped_and_can_be_closed(self) -> None:
        payload = b"Z" * (vpk.MMAP_THRESHOLD + 4096)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pak07_dir.vpk"
            path.write_bytes(vpk.build_vpk({"panorama/styles/big.vcss_c": payload}))
            archive = vpk.open_vpk(path)
            self.assertIsInstance(archive.buffer, mmap.mmap)
            self.assertEqual(len(payload), len(archive.extract("panorama/styles/big.vcss_c")))
            with archive as opened:
                self.assertEqual([], vpk.verify_vpk(opened.buffer, {"panorama/styles/big.vcss_c": payload}))
            archive.close()
            self.assertEqual(b"", archive.buffer)



if __name__ == "__main__":
    unittest.main()
