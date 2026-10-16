"""Сквозные тесты автосборщика: prepare → compile → pack → dmm → report.

Настоящий ``resourcecompiler.exe`` живёт только в CSDK на Windows, поэтому шаг
компиляции здесь проходит через фикстуру ``fixtures/fake_compiler.py``
(подставляется в ``--compiler-cmd``). Так проверяется вся оркестрация: раскладка
дерева аддона, имена ``*_c``, содержимое VPK, метаданные, пакет DMM и отчёты.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import COMPILER_CMD_TEMPLATE, FAKE_COMPILER, HUD_FIXTURE, PYTHON, write_fake_game

import build_mod
import dmm_bundle
import vpk


def _run(argv: list[str], keep_streams_separate: bool = False) -> tuple[int, str]:
    """Запустить сборщик и вернуть (код возврата, вывод).

    Логи уходят в stdout, а с ``--json`` — в stderr; для проверок обычно нужен
    весь вывод, поэтому по умолчанию склеиваем оба потока.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = build_mod.main(argv)
    if keep_streams_separate:
        return code, out.getvalue()
    return code, out.getvalue() + err.getvalue()


class PipelineCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "dist"
        self.build = self.tmp / "build"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def base_args(self) -> list[str]:
        return [
            "--hud-xml", str(HUD_FIXTURE),
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out),
            "--build-dir", str(self.build),
            "--version", "9.9.9",
        ]


class CompiledLayoutTest(PipelineCase):
    """Где бы компилятор ни оставил результат — сборщик обязан его найти.

    ``resourcecompiler`` из CSDK пишет не туда, куда просят, а в своё game-дерево,
    поэтому шаг compile перебирает известные раскладки и берёт только свежий вывод.
    """

    def _template(self, output_root: str) -> str:
        return (
            f'"{PYTHON}" "{FAKE_COMPILER}" --source {{source}} '
            f"--content-root {{content_root}} --output-root {output_root} -game {{game}}"
        )

    def test_result_from_csdk_game_tree_is_used(self) -> None:
        csdk = self.tmp / "CSDK"
        (csdk / "game" / "bin_cs2" / "win64").mkdir(parents=True)
        args = [
            "--hud-xml", str(HUD_FIXTURE),
            "--csdk", str(csdk),
            "--compiler-cmd", self._template("{sdk}/game/citadel_addons/{addon}"),
            "--out", str(self.out), "--build-dir", str(self.build), "--steps", "prepare,compile,pack",
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        archive = vpk.open_vpk(self.out / "bhop_trainer_dir.vpk")
        self.assertIn("panorama/styles/bhop_trainer.vcss_c", archive.list_entries())
        self.assertIn("дерево CSDK", output)

    def test_result_from_game_tree_is_used_and_cleaned_up(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", HUD_FIXTURE.read_bytes())
        args = [
            "--game-path", str(game),
            "--compiler-cmd", self._template("{game}/.."),
            "--out", str(self.out), "--build-dir", str(self.build), "--steps", "prepare,compile,pack",
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        self.assertIn("дерево игры", output)
        # За собой убираем: в файлах игры не должно остаться наших ресурсов.
        leftovers = list((game / "game").rglob("*.vxml_c")) + list((game / "game").rglob("*.vcss_c"))
        self.assertEqual([], leftovers, leftovers)

    def test_missing_result_points_to_compiled_from(self) -> None:
        args = [
            "--hud-xml", str(HUD_FIXTURE),
            "--compiler-cmd", self._template(str(self.tmp / "nowhere")),
            "--out", str(self.out), "--build-dir", str(self.build), "--steps", "prepare,compile",
        ]
        code, output = _run(args)
        self.assertEqual(2, code, output)
        self.assertIn("--compiled-from", output)

    def test_explicit_compiled_from_is_used(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        args = [
            "--hud-xml", str(HUD_FIXTURE),
            "--compiler-cmd", self._template(str(elsewhere)),
            "--compiled-from", str(elsewhere),
            "--out", str(self.out), "--build-dir", str(self.build), "--steps", "prepare,compile,pack",
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        self.assertIn("--compiled-from", output)


class FullPipelineTest(PipelineCase):
    def test_builds_vpk_bundle_and_report(self) -> None:
        code, output = _run(self.base_args())
        self.assertEqual(0, code, output)

        vpk_path = self.out / "bhop_trainer_dir.vpk"
        self.assertTrue(vpk_path.is_file(), output)
        archive = vpk.open_vpk(vpk_path)
        entries = set(archive.list_entries())
        self.assertEqual(
            {
                "addoninfo.txt",
                "modinfo.json",
                "panorama/layout/hud.vxml_c",
                "panorama/styles/bhop_trainer.vcss_c",
            },
            entries,
        )
        # в VPK попал именно скомпилированный ресурс, а не копия исходника
        self.assertTrue(archive.extract("panorama/styles/bhop_trainer.vcss_c").startswith(b"FAKE-COMPILED"))
        self.assertTrue(archive.extract("panorama/layout/hud.vxml_c").startswith(b"FAKE-COMPILED"))
        # и в скомпилированном layout действительно есть патч мода
        self.assertIn(b"bhopt_root", archive.extract("panorama/layout/hud.vxml_c"))

        # пакет DMM
        bundle = self.out / "dmm-bundle"
        self.assertEqual([], dmm_bundle.verify_bundle(bundle))
        document = json.loads((bundle / dmm_bundle.MANIFEST_FILENAME).read_text(encoding="utf-8"))
        mod = document["mods"][0]
        self.assertEqual(f"local:{vpk.sha256_of_file(vpk_path)}", mod["key"])
        self.assertEqual("bhop_trainer_dir.vpk", mod["files"][0]["name"])
        self.assertTrue(mod["files"][0]["path"].startswith("files/"))
        self.assertEqual(mod["files"][0]["size"], vpk_path.stat().st_size)

        # артефакты и отчёты
        self.assertTrue((self.out / "bhop_trainer-dmm.zip").is_file())
        sums = (self.out / "SHA256SUMS.txt").read_text(encoding="utf-8")
        self.assertIn("bhop_trainer_dir.vpk", sums)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        for needle in ("9.9.9", "Open interchange bundle", "citadel/addons", "готов к установке"):
            self.assertIn(needle, report)

        state = json.loads((self.build / "build_state.json").read_text(encoding="utf-8"))
        self.assertIn("compiled", state)
        self.assertEqual(vpk.sha256_of_file(vpk_path), state["vpk_sha256"])

    def test_prepare_only_writes_addon_tree(self) -> None:
        code, output = _run(self.base_args() + ["--steps", "prepare"])
        self.assertEqual(0, code, output)
        content = self.build / "content" / "citadel_addons" / "bhop_trainer"
        layout = (content / "panorama" / "layout" / "hud.xml").read_text(encoding="utf-8")
        self.assertIn("bhop-trainer:begin", layout)
        self.assertIn('<include src="s2r://panorama/styles/bhop_trainer.vcss" />', layout)
        self.assertTrue((content / "panorama" / "styles" / "bhop_trainer.vcss").is_file())
        self.assertTrue((self.build / "vanilla" / "hud.xml").is_file())
        self.assertFalse((self.out / "bhop_trainer_dir.vpk").exists())

    def test_explicit_game_path_extracts_vanilla_hud(self) -> None:
        from _support import write_fake_game

        game = write_fake_game(self.tmp / "Deadlock", HUD_FIXTURE.read_bytes())
        code, output = _run([
            "--game-path", str(game),
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(0, code, output)
        state = json.loads((self.build / "build_state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["hud_source"].endswith("::panorama/layout/hud.xml"))
        self.assertIn("паки игры", state["hud_source_label"])

    def test_json_output_is_machine_readable(self) -> None:
        code, output = _run(self.base_args() + ["--json"], keep_streams_separate=True)
        self.assertEqual(0, code, output)
        payload = json.loads(output)
        self.assertEqual("ok", payload["status"])
        self.assertFalse(payload["sourcesOnly"])
        self.assertEqual([], payload["errors"])

    def test_version_and_addon_name_are_applied(self) -> None:
        code, output = _run(self.base_args() + ["--addon-name", "bhopt_dev", "--title", "Мой тренер"])
        self.assertEqual(0, code, output)
        archive = vpk.open_vpk(self.out / "bhopt_dev_dir.vpk")
        addoninfo = archive.extract("addoninfo.txt").decode("utf-8")
        self.assertIn('addontitle "Мой тренер"', addoninfo)
        self.assertIn('addonversion "9.9.9"', addoninfo)


class MissingCompilerTest(PipelineCase):
    def test_stops_with_needs_compile_status(self) -> None:
        code, output = _run([
            "--hud-xml", str(HUD_FIXTURE),
            "--csdk", str(self.tmp / "no-such-csdk"),
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(build_mod.EXIT_NEEDS_COMPILE, code, output)
        self.assertIn("resourcecompiler", output)
        self.assertFalse((self.out / "bhop_trainer_dir.vpk").exists())
        # отчёт всё равно пишется: понятно, что именно не хватило
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("compile", report)

    def test_allow_uncompiled_produces_sources_only_artifacts(self) -> None:
        code, output = _run([
            "--hud-xml", str(HUD_FIXTURE),
            "--allow-uncompiled",
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(0, code, output)
        sources_vpk = self.out / "sources-only" / "bhop_trainer_dir.vpk"
        self.assertTrue(sources_vpk.is_file())
        self.assertFalse((self.out / "dmm-bundle").exists())
        archive = vpk.open_vpk(sources_vpk)
        self.assertTrue(archive.contains("panorama/styles/bhop_trainer.vcss"))
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("только исходники", report)


class FailureTest(PipelineCase):
    def test_missing_hud_source_is_a_clean_error(self) -> None:
        empty_game = self.tmp / "empty-game"
        (empty_game / "game" / "citadel").mkdir(parents=True)
        (empty_game / "game" / "citadel" / "gameinfo.gi").write_text("// empty\n", encoding="utf-8")
        code, output = _run(["--game-path", str(empty_game), "--out", str(self.out), "--build-dir", str(self.build)])
        self.assertEqual(build_mod.EXIT_ERROR, code, output)
        self.assertIn("hud.xml", output)

    def test_unknown_step_is_rejected(self) -> None:
        code, output = _run(["--steps", "prepare,deploy", "--hud-xml", str(HUD_FIXTURE)])
        self.assertEqual(build_mod.EXIT_ERROR, code, output)
        self.assertIn("неизвестные шаги", output)

    def test_bad_addon_name_is_rejected(self) -> None:
        code, output = _run(["--addon-name", "Bad-Name", "--hud-xml", str(HUD_FIXTURE)])
        self.assertEqual(build_mod.EXIT_ERROR, code, output)
        self.assertIn("addon-name", output)


if __name__ == "__main__":
    unittest.main()
