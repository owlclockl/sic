"""Тесты патча ``hud.xml``: вставка панели, include стилей, идемпотентность."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import HUD_FIXTURE, MOD_ROOT, write_fake_game

import hud_patch

SNIPPET = '<Panel class="bhopt_root">\n  <Label class="bhopt_brand" text="DEADLOCK :: BHOP TRAINER" />\n</Panel>'


def _vanilla() -> str:
    return HUD_FIXTURE.read_text(encoding="utf-8")


class LoadSnippetTest(unittest.TestCase):
    def test_strips_leading_comment_block(self) -> None:
        snippet = hud_patch.load_snippet(MOD_ROOT / "src" / "bhop_trainer.vxml")
        self.assertTrue(snippet.lstrip().startswith("<Panel class=\"bhopt_root\""))
        self.assertIn("DEADLOCK :: BHOP TRAINER", snippet)
        self.assertNotIn("Куда вставлять", snippet)

    def test_rejects_snippet_with_citadel_hud_tag(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.vxml"
            path.write_text("<Panel />\n</CitadelHud>\n", encoding="utf-8")
            with self.assertRaises(hud_patch.HudPatchError):
                hud_patch.load_snippet(path)

    def test_rejects_empty_snippet(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.vxml"
            path.write_text("<!-- только комментарий -->\n", encoding="utf-8")
            with self.assertRaises(hud_patch.HudPatchError):
                hud_patch.load_snippet(path)


class PatchTest(unittest.TestCase):
    def test_patch_inserts_panel_and_style_include(self) -> None:
        result = hud_patch.patch_hud_xml(_vanilla(), snippet=SNIPPET)
        self.assertTrue(result.added_panel)
        self.assertTrue(result.added_styles)

        text = result.text
        self.assertEqual(1, text.count(hud_patch.MARK_BEGIN))
        self.assertEqual(1, text.count(hud_patch.MARK_END))
        self.assertEqual(1, text.count(hud_patch.MARK_STYLES))
        self.assertIn('<include src="s2r://panorama/styles/bhop_trainer.vcss" />', text)

        # include стилей стоит внутри блока <styles> ...
        self.assertLess(text.index(hud_patch.MARK_STYLES), text.index("</styles>"))
        # ... а панель — внутри CitadelHud, перед его закрытием
        panel_index = text.index('class="bhopt_root"')
        self.assertLess(text.index("<CitadelHud"), panel_index)
        self.assertLess(panel_index, text.rindex("</CitadelHud>"))
        self.assertLess(text.index(hud_patch.MARK_END), text.rindex("</CitadelHud>"))
        self.assertTrue(hud_patch.is_patched(text))
        self.assertEqual([], hud_patch.validate_hud_xml(text))

    def test_patch_is_idempotent(self) -> None:
        first = hud_patch.patch_hud_xml(_vanilla(), snippet=SNIPPET)
        second = hud_patch.patch_hud_xml(first.text, snippet=SNIPPET)
        self.assertFalse(second.changed)
        self.assertTrue(second.already_patched)
        self.assertEqual(first.text, second.text)

    def test_keeps_crlf_line_endings(self) -> None:
        crlf = _vanilla().replace("\n", "\r\n")
        patched = hud_patch.patch_hud_xml(crlf, snippet=SNIPPET).text
        self.assertIn("\r\n", patched)
        # ни одного «одинокого» \n: все переводы строк остались CRLF
        self.assertEqual(0, patched.count("\n") - patched.count("\r\n"))

    def test_revert_restores_original(self) -> None:
        patched = hud_patch.patch_hud_xml(_vanilla(), snippet=SNIPPET).text
        reverted = hud_patch.revert_hud_xml(patched)
        self.assertTrue(reverted.changed)
        self.assertEqual(_vanilla(), reverted.text)

    def test_revert_on_clean_file_is_noop(self) -> None:
        reverted = hud_patch.revert_hud_xml(_vanilla())
        self.assertFalse(reverted.changed)

    def test_missing_anchors_raise(self) -> None:
        with self.assertRaises(hud_patch.HudPatchError):
            hud_patch.patch_hud_xml("<root>\n\t<CitadelHud />\n</root>\n", snippet=SNIPPET)
        with self.assertRaises(hud_patch.HudPatchError):
            hud_patch.patch_hud_xml("<root>\n\t<styles>\n\t</styles>\n</root>\n", snippet=SNIPPET)

    def test_broken_snippet_is_rejected_by_xml_check(self) -> None:
        with self.assertRaises(hud_patch.HudPatchError):
            hud_patch.patch_hud_xml(_vanilla(), snippet='<Panel class="x">')


class LocateVanillaTest(unittest.TestCase):
    def test_reads_explicit_file(self) -> None:
        path, data = hud_patch.locate_vanilla_hud_xml(None, HUD_FIXTURE)
        self.assertEqual(HUD_FIXTURE, path)
        self.assertEqual(_vanilla().encode("utf-8"), data)

    def test_extracts_from_game_paks(self) -> None:
        with TemporaryDirectory() as tmp:
            game_dir = write_fake_game(Path(tmp), b"<root>\n\t<styles>\n\t</styles>\n</root>\n")
            path, data = hud_patch.locate_vanilla_hud_xml(game_dir, None)
            self.assertIsNone(path)
            self.assertEqual(b"<root>\n\t<styles>\n\t</styles>\n</root>\n", data)

    def test_missing_explicit_file_raises(self) -> None:
        with self.assertRaises(hud_patch.HudPatchError):
            hud_patch.locate_vanilla_hud_xml(None, Path("/nope/does-not-exist.xml"))


if __name__ == "__main__":
    unittest.main()
