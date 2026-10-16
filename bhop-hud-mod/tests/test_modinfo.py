"""Тесты встроенных метаданных ``vpk-modinfo v1``."""

from __future__ import annotations

import json
import unittest

import _support  # noqa: F401 - добавляет tools/ в sys.path

import modinfo
import vpk

BASE_PAYLOADS = {
    "panorama/layout/hud.vxml_c": b"layout",
    "panorama/styles/bhop_trainer.vcss_c": b"css",
}


def _imprint(**kwargs):
    raw = vpk.build_vpk(BASE_PAYLOADS)
    options = dict(title="Bhop Trainer", author="owlclockl", version="2.1.0",
                   description="line1\nline2", tool="test-tool", tool_version="0.0.1")
    options.update(kwargs)
    return modinfo.imprint(raw, **options)


class ImprintTest(unittest.TestCase):
    def test_writes_both_entries_and_keeps_game_content(self) -> None:
        imprinted, identity = _imprint()
        archive = vpk.VpkArchive(object.__new__(type("P", (), {})) and __import__("pathlib").Path("<mem>"), imprinted)
        entries = set(archive.list_entries())
        self.assertIn("addoninfo.txt", entries)
        self.assertIn("modinfo.json", entries)
        for name, payload in BASE_PAYLOADS.items():
            self.assertEqual(payload, archive.extract(name))
        self.assertEqual(vpk.sha256_of(vpk.build_vpk(BASE_PAYLOADS)), identity.sha256)

    def test_identity_is_pre_imprint_file_hash(self) -> None:
        raw = vpk.build_vpk(BASE_PAYLOADS)
        imprinted, identity = modinfo.imprint(raw, title="T")
        self.assertEqual(vpk.sha256_of(raw), identity.sha256)
        self.assertNotEqual(vpk.sha256_of(imprinted), identity.sha256)
        self.assertEqual(len(raw), identity.size)
        self.assertEqual(f"{vpk.crc32_of(raw):08x}", identity.crc32)

    def test_read_embed_round_trip(self) -> None:
        imprinted, identity = _imprint()
        embed = modinfo.read_embed(imprinted)
        self.assertIsNotNone(embed)
        assert embed is not None
        self.assertEqual(identity, embed.identity)
        self.assertEqual("Bhop Trainer", embed.title)
        self.assertEqual("owlclockl", embed.author)
        self.assertEqual("mod", embed.kind)
        self.assertEqual({"tool": "test-tool", "version": "0.0.1"}, embed.written_by)

    def test_reimprint_carries_identity_and_first_imprint_time(self) -> None:
        first, identity = _imprint(tool="tool-a")
        second, identity_again = _imprint_bytes(first, title="Новое имя", tool="tool-b")
        self.assertEqual(identity.sha256, identity_again.sha256)
        first_embed = modinfo.read_embed(first)
        second_embed = modinfo.read_embed(second)
        assert first_embed and second_embed
        self.assertEqual(first_embed.first_imprinted_at, second_embed.first_imprinted_at)
        self.assertEqual("Новое имя", second_embed.title)
        self.assertEqual("tool-b", second_embed.written_by["tool"])

    def test_refuses_foreign_addoninfo(self) -> None:
        raw = vpk.build_vpk({**BASE_PAYLOADS, "addoninfo.txt": b'"AddonInfo"\n{\n    addontitle "Someone else"\n}\n'})
        with self.assertRaises(modinfo.ModInfoError):
            modinfo.imprint(raw, title="T")

    def test_refuses_multichunk_vpk(self) -> None:
        from _support import craft_vpk

        data = craft_vpk([("a/b.vcss", 0, 0, 0, 4)])
        with self.assertRaises(modinfo.ModInfoError) as ctx:
            modinfo.imprint(data, title="T")
        self.assertIn("многокомпонентный", str(ctx.exception))

    def test_read_embed_returns_none_for_plain_vpk(self) -> None:
        self.assertIsNone(modinfo.read_embed(vpk.build_vpk(BASE_PAYLOADS)))

    def test_read_embed_tolerates_broken_json(self) -> None:
        data, _ = _imprint()
        archive = vpk.VpkArchive(__import__("pathlib").Path("<mem>"), data)
        payloads = {name: archive.extract(name) for name in archive.list_entries()}
        payloads["modinfo.json"] = b"{ not json"
        embed = modinfo.read_embed(vpk.build_vpk(payloads))
        # KV1-идентичность переживает сломанный JSON (addoninfo.txt самодостаточен)
        self.assertIsNotNone(embed)
        assert embed is not None
        self.assertEqual("", embed.title)


def _imprint_bytes(raw: bytes, **kwargs) -> tuple[bytes, modinfo.Identity]:
    return modinfo.imprint(raw, **kwargs)


class AddonInfoFormatTest(unittest.TestCase):
    def test_key_order_indent_and_trailing_newline(self) -> None:
        text = modinfo.render_addoninfo(
            title="T", author="A", version="3", build_date="2026-01-01T00:00:00.000Z",
            identity=modinfo.Identity(sha256="a" * 64, size=10, crc32="deadbeef"),
        )
        self.assertTrue(text.endswith("}\n"))
        lines = text.splitlines()
        self.assertEqual('"AddonInfo"', lines[0])
        self.assertEqual("{", lines[1])
        self.assertEqual("}", lines[-1])
        keys = [line.strip().split(" ")[0] for line in lines[2:-1]]
        self.assertEqual(
            ["addonversion", "addontitle", "addonauthor", "buildDate",
             "originalSha256", "originalSize", "originalCrc32", "modinfoVersion"],
            keys,
        )
        for line in lines[2:-1]:
            self.assertTrue(line.startswith("    "), line)
            self.assertRegex(line, r'^    \w+ "[^"]*"$')

    def test_single_line_rule_collapses_control_characters(self) -> None:
        text = modinfo.render_addoninfo(
            title="line1\nline2\t  x", author="a\"b\\c", version="1",
            build_date="2026-01-01T00:00:00.000Z",
            identity=modinfo.Identity("b" * 64, 1, "00000000"),
        )
        title_line = next(line for line in text.splitlines() if line.strip().startswith("addontitle"))
        self.assertEqual('    addontitle "line1 line2 x"', title_line)
        author_line = next(line for line in text.splitlines() if line.strip().startswith("addonauthor"))
        self.assertEqual('    addonauthor "a\\"b\\\\c"', author_line)
        self.assertNotIn("\t", text)

    def test_omits_empty_optional_keys(self) -> None:
        text = modinfo.render_addoninfo(
            title="T", author="", version="1", build_date="2026-01-01T00:00:00.000Z",
            identity=modinfo.Identity("c" * 64, 1, "00000000"),
        )
        self.assertNotIn("addonauthor", text)


class ModInfoJsonTest(unittest.TestCase):
    def test_schema_and_key_order(self) -> None:
        text = modinfo.render_modinfo(
            title="T", author="A", version="1", description="d",
            tool="tool", tool_version="9", identity=modinfo.Identity("d" * 64, 5, "00000001"),
            written_at="2026-01-01T00:00:00.000Z", first_imprinted_at="2025-01-01T00:00:00.000Z",
        )
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(
            ["format", "schemaVersion", "kind", "writtenBy", "writtenAt",
             "firstImprintedAt", "game", "identity", "title", "author", "description"],
            list(json.loads(text)),
        )
        record = json.loads(text)
        self.assertEqual("vpk-modinfo", record["format"])
        self.assertEqual(1, record["schemaVersion"])
        self.assertEqual({"name": "Deadlock", "steamAppId": 1422450, "gameBananaGameId": 20948}, record["game"])
        self.assertRegex(record["identity"]["sha256"], r"^[0-9a-f]{64}$")

    def test_timestamps_are_iso8601_with_milliseconds(self) -> None:
        text = modinfo.render_modinfo(
            title="T", author="", version="1", description="", tool="t", tool_version="1",
            identity=modinfo.Identity("e" * 64, 1, ""),
            written_at="2026-01-01T00:00:00.000Z", first_imprinted_at="2026-01-01T00:00:00.000Z",
        )
        record = json.loads(text)
        self.assertRegex(record["writtenAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        self.assertNotIn("author", record)
        self.assertNotIn("description", record)


if __name__ == "__main__":
    unittest.main()
