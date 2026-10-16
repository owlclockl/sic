"""Раскладки CSDK: где искать ``resourcecompiler`` и ``gameinfo.gi``.

Раскладки у сборок CSDK разные, и это не косметика: от того, какой каталог
признан корнем CSDK, зависит, куда сборщик положит исходники
(``content/citadel_addons/<addon>``) и откуда заберёт результат
(``game/citadel_addons/<addon>``). Здесь же проверяется, что «новые» раскладки с
бинарниками в корне (``bin/win64`` в Reduced CSDK 12) не заставляют считать
корнем CSDK его же подкаталог ``game``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path

import paths


class CompilerLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, *parts: str, data: bytes = b"MZ fake compiler") -> Path:
        target = self.tmp.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def test_flat_layout_with_bin_in_root_is_found(self) -> None:
        """Текущий Reduced CSDK 12: ``bin/win64`` + ``content``/``game`` в корне."""
        self._write("Reduced_CSDK_12", "bin", "win64", "resourcecompiler.exe")
        (self.tmp / "Reduced_CSDK_12" / "content" / "citadel_addons").mkdir(parents=True)
        (self.tmp / "Reduced_CSDK_12" / "game" / "citadel_addons").mkdir(parents=True)
        root = self.tmp / "Reduced_CSDK_12"
        self.assertEqual(root / "bin" / "win64" / "resourcecompiler.exe", paths.csdk_compiler(root))
        self.assertEqual(root, paths.find_csdk(root))

    def test_game_subdirectory_is_not_mistaken_for_the_root(self) -> None:
        """``<CSDK>/game`` — подкаталог старой раскладки, а не корень CSDK.

        Без этой проверки ``<CSDK>/game/bin_cs2/win64/resourcecompiler.exe``
        «находится» по правилу корневых ``bin*``, и сборщик начинает работать с
        чужим корнем: исходники уезжают в ``<CSDK>/game/content/...``.
        """
        compiler = self._write("csdk", "game", "bin_cs2", "win64", "resourcecompiler.exe")
        root = self.tmp / "csdk"
        game = root / "game"
        self.assertEqual(compiler, paths.csdk_compiler(root))
        self.assertIsNone(paths.csdk_compiler(game))

    def test_all_known_historic_layouts_are_found(self) -> None:
        for index, relative in enumerate(
            (
                "game/bin_cs2/win64",
                "game/bin/win64",
                "game/bin_tools/win64",
                "game/bin_server/win64",
            )
        ):
            with self.subTest(relative=relative):
                root = self.tmp / f"historic-{index}"
                compiler = self._write(str(root / relative), "resourcecompiler.exe")
                self.assertEqual(compiler, paths.csdk_compiler(root))

    def test_all_known_root_layouts_are_found(self) -> None:
        for index, relative in enumerate(
            ("bin/win64", "bin_cs2/win64", "bin_tools/win64", "bin_server/win64")
        ):
            with self.subTest(relative=relative):
                root = self.tmp / f"flat-{index}"
                compiler = self._write(str(root / relative), "resourcecompiler.exe")
                (root / "content" / "citadel_addons").mkdir(parents=True)
                self.assertEqual(compiler, paths.csdk_compiler(root))

    def test_layout_describes_what_is_inside(self) -> None:
        """``csdk_layout`` — то, по чему сборщик решает, нужен ли базовый мод.

        Именно отсутствие ``game/citadel/gameinfo.gi`` заставляло сборщик уходить
        на ``-game`` с деревом установленной игры и получать ``error 126``.
        """
        flat = self.tmp / "flat"
        self._write(str(flat), "bin", "win64", "resourcecompiler.exe")
        (flat / "content" / "citadel_addons").mkdir(parents=True)
        layout = paths.csdk_layout(flat)
        self.assertEqual("flat", layout.layout)
        self.assertFalse(layout.has_gameinfo)
        self.assertFalse(layout.has_modules)
        self.assertIn("gameinfo.gi: нет", layout.describe())

        historic = self.tmp / "historic"
        self._write(str(historic), "game", "bin_cs2", "win64", "resourcecompiler.exe")
        self._write(str(historic), "game", "bin_cs2", "win64", "modtools.dll")
        (historic / "game" / "citadel").mkdir(parents=True)
        (historic / "game" / "citadel" / "gameinfo.gi").write_text("// base mod", encoding="utf-8")
        layout = paths.csdk_layout(historic)
        self.assertEqual("game", layout.layout)
        self.assertTrue(layout.has_gameinfo)
        self.assertTrue(layout.has_modules)
        self.assertEqual(historic / "game" / "citadel" / "gameinfo.gi", layout.gameinfo)
        self.assertIn("gameinfo.gi: есть", layout.describe())
        self.assertIn("модули инструментов: есть", layout.describe())

    def test_missing_vc_runtime_is_only_checked_on_windows(self) -> None:
        """На Linux проверять ``System32`` нечего — список обязан быть пустым."""
        if sys.platform.startswith("win"):
            self.skipTest("на Windows проверка зависит от установленных DLL")
        self.assertEqual([], paths.missing_vc_runtime_dlls())

    def test_extensionless_compiler_is_found(self) -> None:
        """На Linux/Proton компилятор может лежать без ``.exe``."""
        compiler = self._write("csdk", "game", "bin", "win64", "resourcecompiler")
        self.assertEqual(compiler, paths.csdk_compiler(self.tmp / "csdk"))

    def test_directory_named_like_compiler_is_not_a_compiler(self) -> None:
        (self.tmp / "csdk" / "game" / "bin" / "win64" / "resourcecompiler").mkdir(parents=True)
        self.assertIsNone(paths.csdk_compiler(self.tmp / "csdk"))

    def test_find_csdk_rejects_a_directory_without_compiler(self) -> None:
        (self.tmp / "not-csdk" / "game").mkdir(parents=True)
        self.assertIsNone(paths.csdk_compiler(self.tmp / "not-csdk"))
        self.assertIsNone(paths.find_csdk(self.tmp / "not-csdk"))

    def test_gameinfo_helpers_point_into_game_citadel(self) -> None:
        root = self.tmp / "Reduced_CSDK_12"
        self.assertEqual(root / "game" / "citadel", paths.csdk_game_dir(root))
        self.assertEqual(root / "game" / "citadel" / "gameinfo.gi", paths.csdk_gameinfo(root))


if __name__ == "__main__":
    unittest.main()
