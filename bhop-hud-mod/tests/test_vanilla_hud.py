"""Тесты поиска ванильного ``hud.xml``: цепочка источников, кэш, скачивание.

Главный сценарий, который здесь закреплён: в паках настоящей игры **нет**
исходного ``panorama/layout/hud.xml`` — там только скомпилированный
``panorama/layout/hud.vxml_c``. Поэтому сборщик обязан уметь брать исходник из
другого места и внятно объяснять, что делать, если не вышло.
"""

from __future__ import annotations

import base64
import json
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import HUD_FIXTURE, write_fake_game, write_loose_hud

import vanilla_hud

VANILLA = HUD_FIXTURE.read_bytes()
#: Синтетический «скачанный» файл: структура та же, содержимое своё.
DOWNLOADED = VANILLA.replace(b"<CitadelHud", b"<CitadelHud data-origin=\"mirror\"")


class FetchSpy:
    """Подмена сети: отдаёт файлы по словарю и считает обращения."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url not in self.files:
            raise urllib.error.URLError(f"тест: нет такого URL {url}")
        return self.files[url]

    @property
    def downloads(self) -> list[str]:
        """Обращения к файлу layout (без служебного ``steam.inf``).

        Проверяем вхождение, а не ``endswith``: у ссылки GitHub API хвост
        ``…/hud.xml?ref=master``.
        """
        return [url for url in self.calls if "hud.xml" in url]


HUD_URL = "https://mirror.test/hud.xml"
STEAM_INF_URL = "https://mirror.test/steam.inf"


def _files(hud: bytes = DOWNLOADED, build: str | None = "6785") -> dict[str, bytes]:
    files = {HUD_URL: hud}
    if build:
        files[STEAM_INF_URL] = f"ClientVersion={build}\nProductName=citadel\n".encode("utf-8")
    return files


class ResolveCase(unittest.TestCase):
    def setUp(self) -> None:
        _support.block_network(self)  # сеть в тестах только через FetchSpy/file://
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cache = self.tmp / "build" / "vanilla"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def resolve(self, fetch: FetchSpy, **kwargs: object) -> vanilla_hud.HudSource:
        options: dict[str, object] = {
            "cache_dir": self.cache,
            "urls": [HUD_URL],
            "steam_inf_urls": [STEAM_INF_URL],
            "fetch": fetch,
        }
        options.update(kwargs)
        return vanilla_hud.resolve(**options)  # type: ignore[arg-type]


class ExplicitFileTest(ResolveCase):
    def test_explicit_file_is_used_before_everything(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", VANILLA)
        fetch = FetchSpy(_files())
        source = self.resolve(fetch, explicit=HUD_FIXTURE, game_dir=game)
        self.assertEqual("explicit", source.origin)
        self.assertEqual(VANILLA, source.data)
        self.assertEqual([], fetch.calls)

    def test_missing_explicit_file_raises(self) -> None:
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            self.resolve(FetchSpy(_files()), explicit=self.tmp / "nope.xml")
        self.assertIn("не найден", str(ctx.exception))

    def test_explicit_file_must_look_like_hud(self) -> None:
        path = self.tmp / "not-a-hud.xml"
        path.write_text("<root><Panel /></root>\n", encoding="utf-8")
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            self.resolve(FetchSpy(_files()), explicit=path)
        self.assertIn("CitadelHud", str(ctx.exception))


class LooseFileTest(ResolveCase):
    def test_loose_file_in_game_tree_is_used(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True)
        write_loose_hud(game, VANILLA)
        source = self.resolve(FetchSpy(_files()), game_dir=game)
        self.assertEqual("loose", source.origin)
        self.assertEqual(VANILLA, source.data)

    def test_loose_file_wins_over_raw_pak_entry(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", VANILLA)
        write_loose_hud(game, VANILLA.replace(b"<root>", b"<root data-loose=\"1\">", 1))
        source = self.resolve(FetchSpy(_files()), game_dir=game)
        self.assertEqual("loose", source.origin)

    def test_loose_file_that_is_not_a_hud_is_reported(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True)
        path = game / "game" / "citadel" / "panorama" / "layout" / "hud.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("не xml вовсе\n", encoding="utf-8")
        fetch = FetchSpy(_files())
        source = self.resolve(fetch, game_dir=game)
        # битый loose-файл не должен приниматься за ванильный
        self.assertEqual("download", source.origin)
        self.assertEqual(DOWNLOADED, source.data)


class PakTest(ResolveCase):
    def test_raw_source_inside_paks_is_used(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", VANILLA)
        fetch = FetchSpy(_files())
        source = self.resolve(fetch, game_dir=game)
        self.assertEqual("pak", source.origin)
        self.assertIn("паки игры", source.label)
        self.assertEqual(VANILLA, source.data)
        self.assertEqual([], fetch.calls)

    def test_compiled_only_paks_explain_where_to_get_the_file(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            self.resolve(FetchSpy(_files()), game_dir=game, allow_download=False)
        message = str(ctx.exception)
        self.assertIn("hud.vxml_c", message)  # в паках только скомпилированный ресурс
        self.assertIn("--hud-xml", message)  # и что делать
        self.assertIn("Source 2 Viewer", message)
        self.assertIn("6785", message)  # видно, какая игра проверялась

    def test_no_paks_at_all_is_reported(self) -> None:
        game = self.tmp / "Deadlock"
        (game / "game" / "citadel").mkdir(parents=True)
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            self.resolve(FetchSpy(_files()), game_dir=game, allow_download=False)
        self.assertIn("pak*_dir.vpk", str(ctx.exception))

    def test_broken_pak_is_reported_but_does_not_stop_the_search(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", VANILLA)
        (game / "game" / "citadel" / "pak02_dir.vpk").write_bytes("это не vpk".encode("utf-8"))
        source = self.resolve(FetchSpy(_files()), game_dir=game)
        self.assertEqual("pak", source.origin)


class DownloadTest(ResolveCase):
    def test_download_is_cached(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        fetch = FetchSpy(_files())
        first = self.resolve(fetch, game_dir=game)
        self.assertEqual("download", first.origin)
        self.assertEqual(DOWNLOADED, first.data)
        self.assertEqual(1, len(fetch.downloads))
        self.assertTrue((self.cache / vanilla_hud.CACHE_HUD_NAME).is_file())

        second = self.resolve(fetch, game_dir=game)
        self.assertEqual("cache", second.origin)
        self.assertEqual(DOWNLOADED, second.data)
        self.assertEqual(1, len(fetch.downloads), "второй раз качать не нужно")

    def test_refresh_ignores_cache(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        fetch = FetchSpy(_files())
        self.resolve(fetch, game_dir=game)
        self.resolve(fetch, game_dir=game, refresh=True)
        self.assertEqual(2, len(fetch.downloads))

    def test_url_fallback(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True)
        broken = "https://mirror.test/broken-hud.xml"
        fetch = FetchSpy({HUD_URL: DOWNLOADED})
        source = self.resolve(fetch, game_dir=game, urls=[broken, HUD_URL])
        self.assertEqual("download", source.origin)
        self.assertEqual([broken, HUD_URL], fetch.downloads)

    def test_garbage_download_is_rejected(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True)
        fetch = FetchSpy({HUD_URL: b"<html>not a layout</html>"})
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            self.resolve(fetch, game_dir=game)
        self.assertIn("не похож", str(ctx.exception))
        self.assertFalse((self.cache / vanilla_hud.CACHE_HUD_NAME).exists())

    def test_download_disabled_and_nothing_local(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True)
        fetch = FetchSpy(_files())
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            self.resolve(fetch, game_dir=game, allow_download=False)
        self.assertIn("--no-hud-download", str(ctx.exception))
        self.assertEqual([], fetch.calls)

    def test_mirror_build_mismatch_warns(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6700")
        warnings: list[str] = []
        source = self.resolve(FetchSpy(_files(build="6785")), game_dir=game, warn=warnings.append)
        self.assertEqual("6785", source.build_id)
        self.assertEqual("6700", source.game_build_id)
        self.assertTrue(any("6700" in warning and "6785" in warning for warning in warnings), warnings)

    def test_cache_metadata_is_written(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        self.resolve(FetchSpy(_files()), game_dir=game)
        meta = json.loads((self.cache / vanilla_hud.CACHE_META_NAME).read_text(encoding="utf-8"))
        self.assertEqual(HUD_URL, meta["url"])
        self.assertEqual("6785", meta["gameBuildId"])
        self.assertEqual("6785", meta["mirrorBuildId"])


class StaleCacheTest(ResolveCase):
    def _seed_cache(self, *, fetched_for: str, hud: bytes = DOWNLOADED) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / vanilla_hud.CACHE_HUD_NAME).write_bytes(hud)
        (self.cache / vanilla_hud.CACHE_META_NAME).write_text(
            json.dumps({"url": HUD_URL, "mirrorBuildId": fetched_for, "gameBuildId": fetched_for}),
            encoding="utf-8",
        )

    def test_game_update_makes_cache_stale(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        self._seed_cache(fetched_for="6700")
        fetch = FetchSpy(_files())
        source = self.resolve(fetch, game_dir=game)
        self.assertEqual("download", source.origin)
        self.assertEqual(1, len(fetch.downloads))

    def test_offline_stale_cache_is_used_with_warning(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        self._seed_cache(fetched_for="6700")
        warnings: list[str] = []
        source = self.resolve(FetchSpy(_files()), game_dir=game, allow_download=False,
                              warn=warnings.append)
        self.assertEqual("cache", source.origin)
        self.assertTrue(any("6700" in warning for warning in warnings), warnings)

    def test_same_build_cache_is_reused_silently(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        self._seed_cache(fetched_for="6785")
        fetch = FetchSpy(_files())
        source = self.resolve(fetch, game_dir=game)
        self.assertEqual("cache", source.origin)
        self.assertEqual([], fetch.downloads)

    def test_broken_cache_file_is_ignored(self) -> None:
        game = write_fake_game(self.tmp / "Deadlock", compiled_only=True, build_id="6785")
        self._seed_cache(fetched_for="6785", hud=b"\x00\x01 not a layout")
        fetch = FetchSpy(_files())
        source = self.resolve(fetch, game_dir=game)
        self.assertEqual("download", source.origin)
        self.assertEqual(1, len(fetch.downloads))


class HelpersTest(unittest.TestCase):
    def test_game_build_id_reads_steam_inf(self) -> None:
        with TemporaryDirectory() as tmp:
            game = write_fake_game(Path(tmp), compiled_only=True, build_id="7001")
            self.assertEqual("7001", vanilla_hud.game_build_id(game))
            self.assertIsNone(vanilla_hud.game_build_id(Path(tmp) / "nowhere"))

    def test_check_hud_text_rejects_empty_and_foreign_xml(self) -> None:
        self.assertTrue(vanilla_hud.check_hud_text(""))
        self.assertTrue(vanilla_hud.check_hud_text("<root><CitadelHud /></root>"))
        self.assertEqual([], vanilla_hud.check_hud_text(VANILLA.decode("utf-8")))

    def test_could_override_layout_finds_loose_files(self) -> None:
        with TemporaryDirectory() as tmp:
            game = write_fake_game(Path(tmp), compiled_only=True)
            self.assertEqual([], vanilla_hud.could_override_layout(game))
            path = write_loose_hud(game, VANILLA)
            self.assertEqual([path], vanilla_hud.could_override_layout(game))

    def test_iter_attempted_sources_mentions_every_step(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = write_fake_game(root / "Deadlock", compiled_only=True)
            listed = "\n".join(vanilla_hud.iter_attempted_sources(game, None, root / "cache"))
            for needle in ("дерево игры", "паки:", "кэш:", "зеркало:"):
                self.assertIn(needle, listed)

    def test_fetch_bytes_reads_file_urls(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "hud.xml"
            path.write_bytes(VANILLA)
            self.assertEqual(VANILLA, vanilla_hud.fetch_bytes(path.as_uri()))

    def test_mirror_urls_point_at_the_confirmed_paths(self) -> None:
        # Путь проверен по API GitHub: файл лежит именно там (19 285 Б, ветка master).
        for url in vanilla_hud.MIRROR_URLS:
            self.assertIn("SteamTracking/GameTracking-Deadlock", url)
            self.assertIn("game/citadel/pak01_dir/panorama/layout/hud.xml", url)
        self.assertTrue(any(url.startswith("https://raw.githubusercontent.com/") for url in vanilla_hud.MIRROR_URLS))
        self.assertTrue(any("cdn.jsdelivr.net" in url for url in vanilla_hud.MIRROR_URLS))
        self.assertTrue(any("api.github.com" in url for url in vanilla_hud.MIRROR_URLS))
        for url in vanilla_hud.MIRROR_STEAM_INF_URLS:
            self.assertIn("game/citadel/steam.inf", url)

    def test_github_api_json_is_decoded_to_the_file(self) -> None:
        payload = json.dumps({
            "encoding": "base64",
            "size": len(VANILLA),
            "content": base64.b64encode(VANILLA).decode("ascii"),
        }).encode("utf-8")
        api_url = vanilla_hud.MIRROR_URLS[2]
        self.assertEqual(VANILLA, vanilla_hud.decode_download(api_url, payload))

    def test_other_sources_are_left_alone(self) -> None:
        # raw-хост, CDN и file:// отдают файл как есть — JSON-разбор не должен включаться.
        for url in (vanilla_hud.MIRROR_URLS[0], vanilla_hud.MIRROR_URLS[1], "file:///tmp/hud.xml"):
            self.assertEqual(VANILLA, vanilla_hud.decode_download(url, VANILLA))

    def test_broken_api_answers_are_explained(self) -> None:
        api_url = vanilla_hud.MIRROR_URLS[2]
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            vanilla_hud.decode_download(api_url, b"<html>rate limited</html>")
        self.assertIn("JSON", str(ctx.exception))
        wrong_size = json.dumps({
            "encoding": "base64",
            "size": len(VANILLA) + 1,
            "content": base64.b64encode(VANILLA).decode("ascii"),
        }).encode("utf-8")
        with self.assertRaises(vanilla_hud.HudSourceError) as ctx:
            vanilla_hud.decode_download(api_url, wrong_size)
        self.assertIn("размер файла не совпал", str(ctx.exception))

    def test_download_falls_back_to_github_api_mirror(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = write_fake_game(root / "Deadlock", compiled_only=True, build_id="6785")
            api_url = vanilla_hud.MIRROR_URLS[2]
            payload = json.dumps({
                "encoding": "base64",
                "size": len(DOWNLOADED),
                "content": base64.b64encode(DOWNLOADED).decode("ascii"),
            }).encode("utf-8")
            fetch = FetchSpy({vanilla_hud.MIRROR_STEAM_INF_URLS[2]: b"ClientVersion=6785\n",
                              api_url: payload})
            source = vanilla_hud.resolve(
                game_dir=game, csdk_root=None, explicit=None, cache_dir=root / "cache",
                fetch=fetch, log=lambda _text: None,
            )
            self.assertEqual("download", source.origin)
            self.assertEqual(api_url, source.url)
            self.assertEqual(DOWNLOADED, source.data)
            self.assertEqual("6785", source.build_id)
            # raw-хост и jsdelivr пробуем раньше API
            self.assertEqual([vanilla_hud.MIRROR_URLS[0], vanilla_hud.MIRROR_URLS[1], api_url],
                             fetch.downloads)


if __name__ == "__main__":
    unittest.main()
