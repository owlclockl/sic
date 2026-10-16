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
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import (
    COMPILER_CMD_TEMPLATE,
    FAKE_COMPILER,
    HUD_FIXTURE,
    PYTHON,
    read_compiler_log,
    write_fake_csdk,
    write_fake_game,
)

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
        _support.block_network(self)  # тесты не ходят в сеть: ссылки подставляются явно
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


class PackWithoutCompileTest(PipelineCase):
    """Шаг pack должен работать и без шага compile в этом же запуске."""

    def test_allow_uncompiled_puts_sources_into_sources_only(self) -> None:
        args = [
            "--hud-xml", str(HUD_FIXTURE), "--allow-uncompiled",
            "--steps", "prepare,pack",
            "--out", str(self.out), "--build-dir", str(self.build),
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        archive = vpk.open_vpk(self.out / "sources-only" / "bhop_trainer_dir.vpk")
        self.assertIn("panorama/styles/bhop_trainer.vcss", archive.list_entries())
        self.assertFalse((self.out / "dmm-bundle").exists())

    def test_pack_from_csdk_tree_compiled_by_hand(self) -> None:
        csdk = self.tmp / "CSDK"
        addon = csdk / "game" / "citadel_addons" / "bhop_trainer"
        (addon / "panorama" / "layout").mkdir(parents=True)
        (addon / "panorama" / "layout" / "hud.vxml_c").write_bytes(b"compiled-layout")
        (addon / "panorama" / "styles").mkdir(parents=True)
        (addon / "panorama" / "styles" / "bhop_trainer.vcss_c").write_bytes(b"compiled-styles")
        args = [
            "--hud-xml", str(HUD_FIXTURE), "--csdk", str(csdk),
            "--steps", "prepare,pack",
            "--out", str(self.out), "--build-dir", str(self.build),
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        self.assertIn("дерево CSDK", output)
        archive = vpk.open_vpk(self.out / "bhop_trainer_dir.vpk")
        self.assertIn("panorama/styles/bhop_trainer.vcss_c", archive.list_entries())
        self.assertEqual(b"compiled-styles", archive.extract("panorama/styles/bhop_trainer.vcss_c"))

    def test_pack_without_anything_is_a_clear_error(self) -> None:
        code, output = _run([
            "--hud-xml", str(HUD_FIXTURE), "--steps", "prepare,pack",
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(2, code, output)
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
    # Автоустановка CSDK в тестах отключена: они не должны ходить в сеть.
    def test_stops_with_needs_compile_status(self) -> None:
        code, output = _run([
            "--hud-xml", str(HUD_FIXTURE),
            "--csdk", str(self.tmp / "no-such-csdk"),
            "--no-auto-csdk",
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
        code, output = _run([
            "--game-path", str(empty_game), "--no-hud-download",
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
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


class HudSourceChainTest(PipelineCase):
    """Ванильный hud.xml: в паках игры исходника нет — сборщик берёт его отдельно.

    Настоящая игра кладёт в ``pak01_dir.vpk`` только скомпилированный
    ``panorama/layout/hud.xml`` → ``hud.vxml_c``, поэтому проверяем и загрузку с
    зеркала (через ``file://`` — тесты не ходят в сеть), и работу с кэшем.
    """

    def _mirror_url(self, name: str = "mirror-hud.xml", content: bytes | None = None) -> str:
        path = self.tmp / name
        path.write_bytes(content if content is not None else HUD_FIXTURE.read_bytes())
        return path.as_uri()

    def test_downloads_hud_when_paks_have_only_compiled(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        url = self._mirror_url()
        code, output = _run([
            "--game-path", str(game),
            "--hud-xml-url", url,
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(0, code, output)
        state = json.loads((self.build / "build_state.json").read_text(encoding="utf-8"))
        self.assertEqual("download", state["hud_source_origin"])
        self.assertEqual(url, state["hud_source_url"])
        self.assertIn("зеркало депо", state["hud_source_label"])
        self.assertEqual("6785", state["hud_game_build_id"])
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("download", report)
        self.assertIn(url, report)

    def test_loose_vanilla_file_is_used_without_any_download(self) -> None:
        from _support import write_loose_hud

        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        write_loose_hud(game, HUD_FIXTURE.read_bytes())
        code, output = _run([
            "--game-path", str(game),
            "--hud-xml-url", "https://mirror.invalid/hud.xml",  # обращаться нельзя: файл локальный
            "--no-hud-download",
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(0, code, output)
        state = json.loads((self.build / "build_state.json").read_text(encoding="utf-8"))
        self.assertEqual("loose", state["hud_source_origin"])

    def test_explicit_url_wins_over_no_hud_download(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        url = self._mirror_url("pinned-hud.xml")
        code, output = _run([
            "--game-path", str(game),
            "--hud-xml-url", url,
            "--no-hud-download",  # явная ссылка важнее: качать запрещено, но файл ждут оттуда
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(0, code, output)
        self.assertIn("файл берётся по этой ссылке", output)
        state = json.loads((self.build / "build_state.json").read_text(encoding="utf-8"))
        self.assertEqual("download", state["hud_source_origin"])
        self.assertEqual(url, state["hud_source_url"])

    def test_loose_shadow_is_reported_as_a_warning(self) -> None:
        from _support import write_loose_hud

        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        write_loose_hud(game, HUD_FIXTURE.read_bytes())
        code, output = _run([
            "--game-path", str(game), "--no-hud-download",
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(0, code, output)
        self.assertIn("патч другого мода", output)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("патч другого мода", report)

    def test_cached_hud_is_reused_on_second_run(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        url = self._mirror_url()
        args = [
            "--game-path", str(game), "--hud-xml-url", url,
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        self.assertIn("скачан", output)
        first = vpk.open_vpk(self.out / "bhop_trainer_dir.vpk")
        first_entries = first.list_entries()
        first_layout = first.extract("panorama/layout/hud.vxml_c")

        # второй запуск: файл берётся из кэша, содержимое мода — то же самое
        (self.tmp / "mirror-hud.xml").unlink()
        code, output = _run(args)
        self.assertEqual(0, code, output)
        self.assertIn("из кэша", output)
        second = vpk.open_vpk(self.out / "bhop_trainer_dir.vpk")
        self.assertEqual(first_entries, second.list_entries())
        self.assertEqual(first_layout, second.extract("panorama/layout/hud.vxml_c"))
        state = json.loads((self.build / "build_state.json").read_text(encoding="utf-8"))
        self.assertEqual("cache", state["hud_source_origin"])

    def test_no_source_and_no_download_is_a_clean_error(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        code, output = _run([
            "--game-path", str(game),
            "--no-hud-download",
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
        ])
        self.assertEqual(build_mod.EXIT_ERROR, code, output)
        self.assertIn("hud.xml", output)
        self.assertIn("hud.vxml_c", output)
        self.assertIn("--hud-xml", output)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("hud.xml", report)



#: Тесты с настоящим (по поведению) компилятором запускают его как исполняемый
#: питон-скрипт: на Windows так не получится, а CSDK всё равно только под Windows.
needs_posix_compiler = unittest.skipIf(
    sys.platform.startswith("win"), "на Windows питон-скрипт не исполняется как .exe"
)


def _compiled_entries(dist: Path) -> list[str]:
    archive = vpk.open_vpk(dist / "bhop_trainer_dir.vpk")
    return archive.list_entries()


class CompilerInvocationTest(PipelineCase):
    """Какой командой сборщик зовёт ``resourcecompiler``.

    В Reduced CSDK 12 нет файлов самой игры (``game/citadel/gameinfo.gi``), поэтому
    ``-game`` по умолчанию передавать нельзя: компилятор падает ещё до работы.
    Рабочие пайплайны сообщества (QOLLOCK, Deadlock QoL HUD Mod, Deadlock Stats
    Editor) вызывают его как ``-nop4 -f -i <исходник>``.
    """

    def _context(self, argv: list[str]) -> "build_mod.Context":
        args = build_mod.build_parser().parse_args(argv)
        return build_mod.Context(
            root=_support.MOD_ROOT, out_dir=self.out, build_dir=self.build,
            addon_name="bhop_trainer", version="1.0.0", args=args,
        )

    def _resolver(self) -> "build_mod._CompilerResolver":
        csdk = self.tmp / "CSDK"
        return build_mod._CompilerResolver(
            compiler=csdk / "bin" / "win64" / "resourcecompiler.exe",
            sdk=csdk,
            game_dir=csdk / "game" / "citadel",
            content_root=csdk / "content" / "citadel_addons" / "bhop_trainer",
            output_root=csdk / "game" / "citadel_addons" / "bhop_trainer",
        )

    def test_default_command_has_no_game(self) -> None:
        ctx = self._context(["--hud-xml", str(HUD_FIXTURE)])
        source = self._resolver().content_root / "panorama" / "layout" / "hud.xml"
        command = build_mod._compiler_command(ctx, source, self._resolver())
        self.assertEqual(
            [
                str(self._resolver().compiler),
                "-nop4", "-f", "-i", str(source),
            ],
            command,
        )
        self.assertNotIn("-game", command)

    def test_compiler_game_is_passed_when_asked(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", HUD_FIXTURE.read_bytes())
        citadel = game / "game" / "citadel"
        ctx = self._context(["--hud-xml", str(HUD_FIXTURE), "--compiler-game", str(citadel)])
        source = self._resolver().content_root / "panorama" / "layout" / "hud.xml"
        command = build_mod._compiler_command(
            ctx, source, self._resolver(), game=Path(ctx.args.compiler_game)
        )
        self.assertEqual(
            [
                str(self._resolver().compiler),
                "-game", str(citadel),
                "-nop4", "-f", "-i", str(source),
            ],
            command,
        )

    def test_command_template_placeholder_still_gets_csdk_game(self) -> None:
        ctx = self._context(["--hud-xml", str(HUD_FIXTURE), "--compiler-cmd", "tool -game {game} -i {source}"])
        source = self._resolver().content_root / "panorama" / "layout" / "hud.xml"
        command = build_mod._compiler_command(ctx, source, self._resolver())
        self.assertEqual(["tool", "-game", str(self._resolver().game_dir), "-i", str(source)], command)


@needs_posix_compiler
class StrictCompilerPipelineTest(PipelineCase):
    """Сборка с компилятором, который ведёт себя как настоящий.

    Флаги ``-game`` он понимает, но требует рядом ``gameinfo.gi`` — как Reduced
    CSDK 12, где файлов игры нет. Тесты воспроизводят отказ из трекера
    («Application unable to load gameinfo.gi file from directory "game\\citadel"»)
    и проверяют, что после исправления сборка идёт без ``-game``.
    """

    def setUp(self) -> None:
        super().setUp()
        self.log = self.tmp / "compiler-log.jsonl"
        patcher = mock.patch.dict(os.environ, {"SIC_FAKE_COMPILER_LOG": str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _base(self, csdk: Path, *extra: str) -> list[str]:
        return [
            "--hud-xml", str(HUD_FIXTURE),
            "--csdk", str(csdk),
            "--out", str(self.out), "--build-dir", str(self.build),
            "--steps", "prepare,compile,pack",
            *extra,
        ]

    def test_flat_csdk_without_gameinfo_builds(self) -> None:
        """Текущий Reduced CSDK 12: бинарники в корне, ``game/citadel`` нет."""
        csdk = write_fake_csdk(self.tmp / "Reduced_CSDK_12", layout="flat")
        self.assertFalse((csdk / "game" / "citadel" / "gameinfo.gi").exists())
        code, output = _run(self._base(csdk))
        self.assertEqual(0, code, output)
        entries = _compiled_entries(self.out)
        self.assertIn("panorama/layout/hud.vxml_c", entries)
        self.assertIn("panorama/styles/bhop_trainer.vcss_c", entries)
        invocations = read_compiler_log(self.log)
        self.assertTrue(invocations, "компилятор не вызывался")
        for invocation in invocations:
            self.assertNotIn("-game", invocation["argv"], invocation["argv"])
            self.assertIn("-nop4", invocation["argv"])
            self.assertIn("-f", invocation["argv"])
        self.assertIn("без -game", json.dumps(invocations, ensure_ascii=False) + output)

    def test_historical_csdk_with_gameinfo_builds_without_game_flag(self) -> None:
        """Старая раскладка ``game/bin_cs2/win64`` с gameinfo.gi: тоже без -game."""
        csdk = write_fake_csdk(self.tmp / "Deadlock SDK", layout="game", gameinfo=True)
        code, output = _run(self._base(csdk))
        self.assertEqual(0, code, output)
        for invocation in read_compiler_log(self.log):
            self.assertNotIn("-game", invocation["argv"], invocation["argv"])

    def test_compiler_game_without_gameinfo_reports_the_real_reason(self) -> None:
        """``--compiler-game`` на каталог без gameinfo.gi — та самая ошибка из трекера."""
        csdk = write_fake_csdk(self.tmp / "Reduced_CSDK_12", layout="flat")
        code, output = _run(self._base(csdk, "--compiler-game", str(csdk / "game" / "citadel")))
        self.assertEqual(2, code, output)
        self.assertIn("Can't find 'game\\citadel\\gameinfo.gi'", output)
        self.assertIn("--compiler-game", output)
        # Компилятор вызывался с -game: иначе тест проверял бы не то.
        self.assertIn("-game", read_compiler_log(self.log)[0]["argv"])

        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Статус: **ошибка компиляции (VPK не собран)**", report)
        self.assertNotIn("готов к установке", report)
        self.assertIn("## Ошибки", report)
        self.assertIn("Can't find 'game\\citadel\\gameinfo.gi'", report)

    def test_fallback_to_installed_game_gameinfo(self) -> None:
        """CSDK, которому -game обязателен: сборщик сам берёт gameinfo.gi из игры."""
        csdk = write_fake_csdk(self.tmp / "Reduced_CSDK_12", layout="flat")
        game = write_fake_game(self.tmp / "Deadlock", HUD_FIXTURE.read_bytes())
        patcher = mock.patch.dict(os.environ, {"SIC_FAKE_REQUIRE_GAME": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

        code, output = _run(self._base(csdk, "--game-path", str(game)))
        self.assertEqual(0, code, output)
        invocations = read_compiler_log(self.log)
        first = invocations[0]["argv"]
        fallback = invocations[1]["argv"]
        self.assertNotIn("-game", first)
        self.assertEqual("-game", fallback[fallback.index("-game")])
        self.assertEqual(str(game / "game" / "citadel"), fallback[fallback.index("-game") + 1])
        self.assertIn("не нашёл gameinfo.gi", output)
        # Результат забираем в build/, в дереве игры ничего не остаётся.
        self.assertEqual([], list((game / "game").rglob("*.vxml_c")))
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("gameinfo.gi", report)


class ReportHonestyTest(PipelineCase):
    """Отчёт не должен врать: упавшая сборка — это не «готов к установке»."""

    def test_compiler_failure_report_shows_the_error(self) -> None:
        # «Компилятор», который всегда падает и печатает понятную причину.
        failing = (
            f'"{PYTHON}" -c "import sys; '
            'sys.stderr.write(\\"boom: compile failed\\"); sys.exit(1)"'
        )
        args = [
            "--hud-xml", str(HUD_FIXTURE),
            "--compiler-cmd", failing,
            "--out", str(self.out), "--build-dir", str(self.build),
            "--steps", "prepare,compile",
        ]
        code, output = _run(args)
        self.assertEqual(2, code, output)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Статус: **ошибка компиляции (VPK не собран)**", report)
        self.assertNotIn("готов к установке", report)
        self.assertIn("## Ошибки", report)
        self.assertIn("boom: compile failed", report)
        self.assertNotIn("Сборка выполнялась вне Windows", report)

    def test_compiler_failure_keeps_head_and_full_log(self) -> None:
        # Ключевая строка (как в реальном падении CSDK) стоит в НАЧАЛЕ вывода:
        # раньше отчёт показывал только хвост и её терял.
        script = self.tmp / "noisy_compiler.py"
        script.write_text(
            "import sys\n"
            "print('CAppSystemDict:Unable to load module modtools (Dependency of application), error 126')\n"
            "for i in range(300):\n"
            "    print(f'CMaterial2 noise line {i}')\n"
            "print('final line: giving up')\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        args = [
            "--hud-xml", str(HUD_FIXTURE),
            "--compiler-cmd", f'"{PYTHON}" "{script}"',
            "--out", str(self.out), "--build-dir", str(self.build),
            "--steps", "prepare,compile",
        ]
        code, output = _run(args)
        self.assertEqual(2, code, output)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Unable to load module modtools", report)
        self.assertIn("final line: giving up", report)
        self.assertIn("пропущено", report)
        self.assertIn("--csdk-redownload", report)  # подсказка про сломанный CSDK
        self.assertIn("compile_logs", output)  # ссылка на полный журнал — в консоли
        logs = list((self.build / "compile_logs").glob("*.log"))
        self.assertEqual(1, len(logs))
        full = logs[0].read_text(encoding="utf-8")
        self.assertIn("noise line 299", full)  # полный журнал не урезан

    def test_placeholder_compiler_report_does_not_claim_valve_compiler(self) -> None:
        args = [
            "--hud-xml", str(HUD_FIXTURE),
            "--compiler-cmd", COMPILER_CMD_TEMPLATE,
            "--out", str(self.out), "--build-dir", str(self.build),
            "--steps", "prepare,compile,pack",
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Компиляция подменена шаблоном", report)
        self.assertNotIn("Компиляция выполнена реальным компилятором CSDK", report)

    @needs_posix_compiler
    def test_real_compiler_report_names_the_compiler(self) -> None:
        csdk = write_fake_csdk(self.tmp / "Reduced_CSDK_12", layout="flat")
        args = [
            "--hud-xml", str(HUD_FIXTURE), "--csdk", str(csdk),
            "--out", str(self.out), "--build-dir", str(self.build),
            "--steps", "prepare,compile,pack",
        ]
        code, output = _run(args)
        self.assertEqual(0, code, output)
        report = (self.out / "BUILD_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Компиляцию запускал `resourcecompiler.exe` из CSDK", report)
        self.assertIn(str(csdk / "bin" / "win64" / "resourcecompiler.exe"), report)


class BuildBatTest(unittest.TestCase):
    """Инварианты build.bat: CRLF, только ASCII, кавычки вокруг пути Python."""

    def setUp(self) -> None:
        self.bat_path = _support.MOD_ROOT / "build.bat"
        self.raw = self.bat_path.read_bytes()
        self.text = self.raw.decode("ascii")

    def test_is_pure_ascii_with_crlf_line_endings(self) -> None:
        lines = self.raw.split(b"\n")[:-1]
        self.assertTrue(lines)
        bad_lines = [idx + 1 for idx, line in enumerate(lines) if not line.endswith(b"\r")]
        self.assertEqual([], bad_lines, f"Строки без CRLF: {bad_lines}")

    def test_python_path_with_spaces_is_quoted_in_py_cmd(self) -> None:
        # На Windows PyManager ставит лаунчер в "C:\Program Files\PyManager\py.exe":
        # без кавычек вокруг %_py_full% в PY_CMD cmd.exe обрывается на "'C:\Program' is not recognized".
        self.assertIn('set "PY_CMD="%_py_full%""', self.text)
        self.assertIn('set "PY_CMD="%_py_full%" %~2"', self.text)
        self.assertNotIn('set "PY_CMD=%_py_full%"', self.text)
        self.assertNotIn('set "PY_CMD=%_py_full% %~2"', self.text)

    def test_resolve_csdk_checks_auto_install_root(self) -> None:
        self.assertIn(r"%LOCALAPPDATA%\DeadlockModding\Reduced_CSDK_12", self.text)
        self.assertIn(r"%SystemDrive%\DeadlockModding\Reduced_CSDK_12", self.text)

    def test_run_tests_command_is_valid(self) -> None:
        self.assertIn('unittest discover -s "%MOD_DIR%\\tests" -p "test_*.py"', self.text)
        self.assertNotIn('-t "%MOD_DIR%"', self.text)


    def test_hud_flags_are_parsed_and_forwarded(self) -> None:
        self.assertIn('if /i "%~1"=="--hud-xml-url" set "ARG_HUD_URL=%~2"', self.text)
        self.assertIn('if /i "%~1"=="--refresh-hud-xml" set "DO_HUD_REFRESH=1"', self.text)
        self.assertIn('if /i "%~1"=="--no-hud-download" set "NO_HUD_DOWNLOAD=1"', self.text)
        self.assertIn('set "BUILD_ARGS=%BUILD_ARGS% --hud-xml-url "%ARG_HUD_URL%""', self.text)
        self.assertIn('set "BUILD_ARGS=%BUILD_ARGS% --refresh-hud-xml"', self.text)
        self.assertIn('set "BUILD_ARGS=%BUILD_ARGS% --no-hud-download"', self.text)
        # справка не должна врать про источник ванильного файла
        self.assertIn("--refresh-hud-xml    ignore the cached hud.xml", self.text)
        self.assertNotIn("straight out of the game's .vpk packages", self.text)

    def test_builder_accepts_the_same_flags(self) -> None:
        for flag in ("--hud-xml-url", "--no-hud-download", "--refresh-hud-xml", "--compiler-game"):
            self.assertIn(flag, self.text)  # build.bat (справка или проброс через ARG_EXTRA)
        source = (_support.MOD_ROOT / "tools" / "build_mod.py").read_text(encoding="utf-8")
        for flag in ("--hud-xml-url", "--no-hud-download", "--refresh-hud-xml", "--hud-xml",
                     "--compiler-game"):
            self.assertIn(f'"{flag}"', source)

    def test_batch_retries_after_modtools_dll_error(self) -> None:
        """Windows launcher quarantines the bad SDK, force-downloads, and retries once."""
        self.assertIn('findstr /i /c:"Unable to load module" /c:"error 126"', self.text)
        self.assertIn('set "BUILD_RETRIED=1"', self.text)
        self.assertIn('set "FORCE_CSDK_INSTALL=1"', self.text)
        self.assertIn('--force-install', self.text)
        self.assertIn('move "%BROKEN_CSDK%" "%CSDK_BACKUP%"', self.text)
        self.assertIn('goto :do_build', self.text)

    def test_csdk_layouts_in_bat_match_the_builder(self) -> None:
        """build.bat обязан знать те же раскладки CSDK, что и tools/paths.py."""
        import paths

        relative_parts = {
            Path(relative).parts[:2] for relative in paths.COMPILER_RELATIVE_PATHS
            if len(Path(relative).parts) > 3 and Path(relative).parts[0].startswith("bin")
        }
        for parts in sorted(relative_parts):
            joined = "\\".join(parts)
            self.assertIn(joined, self.text, f"в build.bat нет раскладки {joined}")
        self.assertNotIn("Check that CSDK is unpacked and that it contains", self.text)


if __name__ == "__main__":
    unittest.main()


class CompilerExcerptTest(unittest.TestCase):
    """Выжимка из вывода компилятора: начало и ключевые строки не теряются."""

    def test_short_output_is_kept_whole(self) -> None:
        text = "line one\nline two"
        self.assertEqual(["line one", "line two"], build_mod._output_excerpt(text))

    def test_long_output_keeps_head_error_lines_and_tail(self) -> None:
        lines = ["first line: Unable to load module modtools, error 126"]
        lines += [f"noise {i}" for i in range(200)]
        lines += ["ERROR: something in the middle", "last line"]
        excerpt = build_mod._output_excerpt("\n".join(lines))
        self.assertEqual("first line: Unable to load module modtools, error 126", excerpt[0])
        self.assertEqual("last line", excerpt[-1])
        self.assertIn("ERROR: something in the middle", excerpt)
        self.assertTrue(any("пропущено" in line for line in excerpt))
        self.assertLess(len(excerpt), 60)

    def test_repeated_error_lines_are_deduplicated(self) -> None:
        repeated = ["Error creating shader x.vfx for material m.vmat!"] * 80
        lines = ["head"] * 25 + repeated + ["tail"] * 20
        excerpt = build_mod._output_excerpt("\n".join(lines))
        self.assertEqual(1, sum(1 for line in excerpt if "Error creating shader" in line))


