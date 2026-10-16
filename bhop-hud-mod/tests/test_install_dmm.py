"""Тесты установщика: gameinfo.gi, слоты, проверка результата, cfg, dry-run."""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import write_fake_game

import gameinfo
import install_dmm
import paths
import vpk

MOD_ENTRIES = {
    "panorama/styles/bhop_trainer.vcss_c": b"compiled-css",
    "panorama/layout/hud.vxml_c": b"compiled-layout",
}


def _run(args: list[str]) -> tuple[int, str]:
    # --no-log-file: тесты не должны сорить журналами рядом с dist репозитория
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = install_dmm.main([*args, "--no-log-file"])
    return code, buffer.getvalue()


class FakeGameCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.game = write_fake_game(Path(self._tmp.name) / "Deadlock", b"<root />")
        self.gameinfo = self.game / paths.GAMEINFO_RELATIVE
        self.addons = install_dmm.addons_dir(self.game)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def install_mod(self, name: str = "pak02_dir.vpk", entries: dict[str, bytes] | None = None) -> Path:
        path = self.addons / name
        path.write_bytes(vpk.build_vpk(entries or MOD_ENTRIES))
        return path


class GameInfoTest(FakeGameCase):
    def test_status_reports_missing_addons_path(self) -> None:
        status = gameinfo.status(self.gameinfo)
        self.assertTrue(status.exists)
        self.assertFalse(status.has_addons_path)
        self.assertTrue(status.braces_balanced)
        self.assertFalse(status.ours)

    def test_enable_is_idempotent_and_backs_up(self) -> None:
        first = gameinfo.enable_addons_path(self.gameinfo)
        self.assertTrue(first.changed)
        self.assertIsNotNone(first.backup)
        assert first.backup is not None and first.backup.is_file()

        status = gameinfo.status(self.gameinfo)
        self.assertTrue(status.has_addons_path)
        self.assertTrue(status.ours)
        self.assertTrue(status.braces_balanced)

        second = gameinfo.enable_addons_path(self.gameinfo)
        self.assertFalse(second.changed)

    def test_added_line_sits_before_game_citadel(self) -> None:
        gameinfo.enable_addons_path(self.gameinfo)
        text = self.gameinfo.read_text(encoding="utf-8")
        self.assertLess(text.index("Game                citadel/addons"), text.index("Game                citadel\n"))

    def test_disable_removes_only_our_lines(self) -> None:
        original = self.gameinfo.read_text(encoding="utf-8")
        gameinfo.enable_addons_path(self.gameinfo)
        outcome = gameinfo.disable_addons_path(self.gameinfo)
        self.assertTrue(outcome.changed)
        self.assertEqual(original, self.gameinfo.read_text(encoding="utf-8"))
        self.assertFalse(gameinfo.status(self.gameinfo).has_addons_path)

    def test_disable_from_backup_restores_file(self) -> None:
        gameinfo.enable_addons_path(self.gameinfo)
        self.gameinfo.write_text("сломанный файл {{{", encoding="utf-8")
        outcome = gameinfo.disable_addons_path(self.gameinfo, from_backup=True)
        self.assertTrue(outcome.changed)
        self.assertIn("Game                citadel", self.gameinfo.read_text(encoding="utf-8"))

    def test_refuses_to_patch_unbalanced_file(self) -> None:
        self.gameinfo.write_text('"GameInfo"\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n', encoding="utf-8")
        with self.assertRaises(gameinfo.GameInfoError):
            gameinfo.enable_addons_path(self.gameinfo)

    def test_dry_run_changes_nothing(self) -> None:
        before = self.gameinfo.read_bytes()
        outcome = gameinfo.enable_addons_path(self.gameinfo, dry_run=True)
        self.assertTrue(outcome.changed)
        self.assertTrue(outcome.dry_run)
        self.assertEqual(before, self.gameinfo.read_bytes())

    def test_brace_balance_ignores_strings_and_comments(self) -> None:
        self.assertEqual(0, gameinfo.brace_balance('// { в комментарии\n"a" "}"\n{\n}\n'))


class SlotsAndDetectionTest(FakeGameCase):
    def test_pick_slot_fills_gaps(self) -> None:
        self.assertEqual(1, install_dmm.pick_slot(self.game))
        (self.addons / "pak01_dir.vpk").write_bytes(b"x")
        (self.addons / "pak03_dir.vpk").write_bytes(b"x")
        self.assertEqual(2, install_dmm.pick_slot(self.game))
        self.assertEqual(5, install_dmm.pick_slot(self.game, 5))
        with self.assertRaises(SystemExit):
            install_dmm.pick_slot(self.game, 1)

    def test_find_installed_mods_reads_marker_and_metadata(self) -> None:
        import modinfo

        imprinted, _ = modinfo.imprint(vpk.build_vpk(MOD_ENTRIES), title="Deadlock :: Bhop Trainer")
        (self.addons / "pak04_dir.vpk").write_bytes(imprinted)
        self.addons.joinpath("profile_a").mkdir(exist_ok=True)
        (self.addons / "profile_a" / "pak05_dir.vpk").write_bytes(vpk.build_vpk({"other/file.vcss_c": b"x"}))

        found = install_dmm.find_installed_mods(self.game)
        self.assertEqual(1, len(found))
        self.assertEqual(4, found[0].slot)
        self.assertTrue(found[0].compiled)
        self.assertEqual("Deadlock :: Bhop Trainer", found[0].title)

    def test_find_installed_mods_flags_uncompiled(self) -> None:
        self.install_mod("pak01_dir.vpk", {"panorama/styles/bhop_trainer.vcss": b"raw"})
        found = install_dmm.find_installed_mods(self.game)
        self.assertEqual(1, len(found))
        self.assertFalse(found[0].compiled)

    def test_env_report_finds_game_dir_by_explicit_path(self) -> None:
        found = paths.find_game_dir(self.game)
        self.assertEqual(self.game, found)
        with self.assertRaises(FileNotFoundError):
            paths.find_game_dir(self.game / "nope")


class CommandTest(FakeGameCase):
    def test_check_reports_missing_build_and_lists_next_steps(self) -> None:
        code, output = _run(["--game-path", str(self.game), "check"])
        self.assertEqual(0, code, output)
        self.assertIn("Deadlock:", output)
        self.assertIn("gameinfo.gi", output)
        self.assertIn("Что дальше", output)

    def test_verify_without_mod_returns_1(self) -> None:
        code, output = _run(["--game-path", str(self.game), "verify"])
        self.assertEqual(1, code, output)
        self.assertIn("НЕ найден", output)

    def test_verify_ok_when_mod_and_gameinfo_are_present(self) -> None:
        self.install_mod()
        gameinfo.enable_addons_path(self.gameinfo)
        code, output = _run(["--game-path", str(self.game), "verify"])
        self.assertEqual(0, code, output)
        self.assertIn("pak02_dir.vpk", output)
        self.assertIn("путь citadel/addons включён", output)

    def test_verify_flags_missing_gameinfo_path(self) -> None:
        self.install_mod()
        code, output = _run(["--game-path", str(self.game), "verify"])
        self.assertEqual(1, code, output)
        self.assertIn("gameinfo.gi", output)

    def test_verify_flags_uncompiled_mod(self) -> None:
        self.install_mod("pak01_dir.vpk", {"panorama/styles/bhop_trainer.vcss": b"raw"})
        gameinfo.enable_addons_path(self.gameinfo)
        code, output = _run(["--game-path", str(self.game), "verify"])
        self.assertEqual(1, code, output)
        self.assertIn("исходники", output)

    def test_cfg_dry_run_lists_repo_configs(self) -> None:
        code, output = _run(["--game-path", str(self.game), "cfg", "--dry-run"])
        self.assertEqual(0, code, output)
        self.assertIn("bhop_helper.cfg", output)
        self.assertFalse((self.game / "game" / "citadel" / "cfg" / "bhop_helper.cfg").exists())

    def test_cfg_copies_and_uninstalls(self) -> None:
        code, _ = _run(["--game-path", str(self.game), "cfg"])
        self.assertEqual(0, code)
        target = self.game / "game" / "citadel" / "cfg" / "bhop_helper.cfg"
        self.assertTrue(target.is_file())
        code, _ = _run(["--game-path", str(self.game), "cfg", "--uninstall"])
        self.assertEqual(0, code)
        self.assertFalse(target.exists())


class RestoreGameInfoTest(FakeGameCase):
    """``uninstall --restore-gameinfo`` должен возвращать копию, а не просто убирать строку."""

    def setUp(self) -> None:
        super().setUp()
        gameinfo.enable_addons_path(self.gameinfo)
        # Портим файл так, как это не исправить удалением одной строки.
        self.gameinfo.write_text(
            self.gameinfo.read_text(encoding="utf-8") + "\n// мусор после правки\n",
            encoding="utf-8",
        )

    def test_restore_gameinfo_uses_the_backup(self) -> None:
        code, output = _run(["--game-path", str(self.game), "uninstall", "--restore-gameinfo", "--keep-gameinfo"])
        self.assertEqual(0, code, output)
        text = self.gameinfo.read_text(encoding="utf-8")
        self.assertNotIn("мусор", text)
        self.assertNotIn("citadel/addons", text)

    def test_keep_gameinfo_leaves_the_file_alone(self) -> None:
        before = self.gameinfo.read_text(encoding="utf-8")
        code, output = _run(["--game-path", str(self.game), "uninstall", "--keep-gameinfo"])
        self.assertEqual(0, code, output)
        self.assertEqual(before, self.gameinfo.read_text(encoding="utf-8"))

    def test_plain_uninstall_removes_only_our_block(self) -> None:
        code, output = _run(["--game-path", str(self.game), "uninstall"])
        self.assertEqual(0, code, output)
        text = self.gameinfo.read_text(encoding="utf-8")
        self.assertIn("мусор", text)
        self.assertNotIn("citadel/addons", text)


class GameRunningTest(unittest.TestCase):
    """Проверка «игра запущена»: ищем процесс игры, а не подстроку в командной строке."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.proc = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add_process(self, pid: int, comm: str, *argv: str) -> None:
        entry = self.proc / str(pid)
        entry.mkdir()
        (entry / "comm").write_text(comm + "\n", encoding="utf-8")
        (entry / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")

    def test_own_command_line_with_game_path_is_not_the_game(self) -> None:
        """Раньше установщик «видел» игру в самом себе: путь содержит game/citadel."""
        self._add_process(101, "python3", "/usr/bin/python3")
        self._add_process(102, "bash", "bash", "-c", "python3 tools/install_dmm.py --game-path /mnt/games/Deadlock/game/citadel direct")
        self.assertIs(False, paths.is_game_running(self.proc))

    def test_wine_process_of_the_game_is_detected(self) -> None:
        self._add_process(201, "citadel.exe", "/mnt/games/Deadlock/game/bin/win64/citadel.exe")
        self.assertIs(True, paths.is_game_running(self.proc))

    def test_unrelated_process_with_citadel_in_its_arguments_is_not_the_game(self) -> None:
        """Аргументы процесса не должны считаться признаком игры — только его имя."""
        self._add_process(301, "python3", "/opt/tools/watch.py", "--target", "game/citadel")
        self.assertIs(False, paths.is_game_running(self.proc))


class DirectAndUninstallTest(FakeGameCase):
    def setUp(self) -> None:
        super().setUp()
        # Подкладываем «собранный» VPK туда, откуда его берёт direct-режим.
        self._dist = TemporaryDirectory()
        dist = Path(self._dist.name)
        (dist / "bhop_trainer_dir.vpk").write_bytes(vpk.build_vpk(MOD_ENTRIES))
        self._saved = install_dmm.VPK_SOURCE
        install_dmm.VPK_SOURCE = dist / "bhop_trainer_dir.vpk"

    def tearDown(self) -> None:
        install_dmm.VPK_SOURCE = self._saved
        self._dist.cleanup()
        super().tearDown()

    def test_direct_dry_run_writes_nothing(self) -> None:
        code, output = _run(["--game-path", str(self.game), "direct", "--dry-run"])
        self.assertEqual(0, code, output)
        self.assertIn("Режим --dry-run", output)
        self.assertEqual([], list(self.addons.glob("*.vpk")))
        self.assertFalse(gameinfo.status(self.gameinfo).has_addons_path)

    def test_direct_installs_and_patches_gameinfo(self) -> None:
        code, output = _run(["--game-path", str(self.game), "direct"])
        self.assertEqual(0, code, output)
        installed = sorted(path.name for path in self.addons.glob("pak*_dir.vpk"))
        self.assertEqual(["pak01_dir.vpk"], installed)
        self.assertTrue(gameinfo.status(self.gameinfo).has_addons_path)
        self.assertIn("Готово", output)

    def test_direct_requires_built_vpk(self) -> None:
        install_dmm.VPK_SOURCE = Path("/nope/missing.vpk")
        code, output = _run(["--game-path", str(self.game), "direct"])
        self.assertEqual(2, code, output)
        self.assertIn("нет собранного VPK", output)

    def test_uninstall_removes_mod_and_restores_gameinfo(self) -> None:
        _run(["--game-path", str(self.game), "direct"])
        code, output = _run(["--game-path", str(self.game), "uninstall", "--restore-gameinfo"])
        self.assertEqual(0, code, output)
        self.assertEqual([], list(self.addons.glob("pak*_dir.vpk")))
        self.assertFalse(gameinfo.status(self.gameinfo).has_addons_path)

    def test_uninstall_dry_run_keeps_files(self) -> None:
        _run(["--game-path", str(self.game), "direct"])
        code, output = _run(["--game-path", str(self.game), "uninstall", "--dry-run"])
        self.assertEqual(0, code, output)
        self.assertIn("[dry-run]", output)
        self.assertEqual(1, len(list(self.addons.glob("pak*_dir.vpk"))))


if __name__ == "__main__":
    unittest.main()
