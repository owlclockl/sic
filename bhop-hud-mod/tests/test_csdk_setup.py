"""Тесты автоустановки CSDK: поиск, скачивание, распаковка, раскладка.

Сеть не нужна: вместо Google Диска поднимается локальный HTTP-сервер, который
умеет и обычную отдачу, и догрузку по Range, и страницу подтверждения. Архивы
синтетические — игровые файлы CSDK (и сам ``resourcecompiler.exe``) в репозитории
не хранятся.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import PYTHON, run_cli

import csdk_setup
import paths

COMPILER_RELATIVE = Path("game") / "bin_cs2" / "win64" / "resourcecompiler.exe"

#: Прогресс установки в тестах не нужен: он печатается в вывод unittest.
_quiet = lambda message: None  # noqa: E731


def make_fake_archive(archive: Path, *, root_dir: str = "Reduced_CSDK_12", nested: bool = True) -> Path:
    """Собрать zip «как настоящий»: внутри — папка ``Reduced_CSDK_12`` с CSDK."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        (f"{root_dir}/{COMPILER_RELATIVE.as_posix()}" if nested else COMPILER_RELATIVE.as_posix(),
         b"MZ fake resourcecompiler"),
        (f"{root_dir}/csdkcfg.exe" if nested else "csdkcfg.exe", b"MZ fake cfg"),
        (f"{root_dir}/game/citadel/gameinfo.gi" if nested else "game/citadel/gameinfo.gi", b"// gameinfo"),
    ]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in payload:
            zipped.writestr(name, data)
    return archive


class _Handler(BaseHTTPRequestHandler):
    """Отдаёт либо HTML подтверждения, либо файл (с поддержкой Range)."""

    payload = b""
    html: str | None = None
    html_times = 0
    served_html = 0
    requests: list[str] = []
    ranges: list[str] = []
    filename = "Reduced_CSDK_12.zip"

    def log_message(self, *args: object) -> None:  # тишина в выводе тестов
        return

    def do_GET(self) -> None:  # noqa: N802 - имя задаёт BaseHTTPRequestHandler
        type(self).requests.append(self.path)
        type(self).ranges.append(str(self.headers.get("Range") or ""))
        if self.html is not None and self.served_html < self.html_times:
            type(self).served_html += 1
            body = self.html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = self.payload
        status = 200
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            start = int(range_header[len("bytes="):].split("-")[0] or 0)
            if start <= len(body):
                body = self.payload[start:]
                status = 206
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
            else:
                self.send_response(416)
                self.end_headers()
                return
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{self.filename}"')
        self.end_headers()
        self.wfile.write(body)


class LocalServer:
    """Локальный HTTP-сервер для тестов скачивания."""

    def __init__(self) -> None:
        handler = type("Handler", (_Handler,), {})
        handler.requests = []
        handler.ranges = []
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/uc?id=test"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class InstallDirTest(unittest.TestCase):
    """Куда CSDK класть нельзя (и почему)."""

    def test_one_drive_is_rejected(self) -> None:
        self.assertIsNotNone(csdk_setup.unsafe_dir_reason(r"C:\Users\me\OneDrive\csdk"))

    def test_desktop_and_documents_are_rejected(self) -> None:
        for path in (r"C:\Users\me\Desktop\csdk", r"C:\Users\me\Documents\csdk", "/home/me/Dropbox/csdk"):
            with self.subTest(path=path):
                self.assertIsNotNone(csdk_setup.unsafe_dir_reason(path))

    def test_plain_directory_is_fine(self) -> None:
        self.assertIsNone(csdk_setup.unsafe_dir_reason("/opt/deadlock-csdk"))
        self.assertIsNone(csdk_setup.unsafe_dir_reason(r"C:\DeadlockModding\Reduced_CSDK_12"))

    def test_default_install_root_is_safe(self) -> None:
        root = csdk_setup.default_install_root()
        self.assertIsNone(csdk_setup.unsafe_dir_reason(root), root)
        self.assertEqual("Reduced_CSDK_12", root.name)

    def test_cache_dir_sits_next_to_install_root(self) -> None:
        root = csdk_setup.default_install_root()
        self.assertEqual(root.parent / "csdk-cache", csdk_setup.default_cache_dir(root))


class ArchiveKindTest(unittest.TestCase):
    def test_kind_is_detected_by_magic_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a.zip").write_bytes(b"PK\x03\x04" + b"\0" * 32)
            (base / "a.7z").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 32)
            (base / "a.rar").write_bytes(b"Rar!\x1a\x07\x00" + b"\0" * 32)
            (base / "a.tar.gz").write_bytes(b"\x1f\x8b\x08" + b"\0" * 32)
            self.assertEqual("zip", csdk_setup.archive_kind(base / "a.zip"))
            self.assertEqual("7z", csdk_setup.archive_kind(base / "a.7z"))
            self.assertEqual("rar", csdk_setup.archive_kind(base / "a.rar"))
            self.assertEqual("gz", csdk_setup.archive_kind(base / "a.tar.gz"))

    def test_unknown_format_is_a_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            junk = Path(tmp) / "csdk.bin"
            junk.write_bytes(b"just a text file, definitely not an archive")
            with self.assertRaises(csdk_setup.CsdkError) as caught:
                csdk_setup.archive_kind(junk)
            self.assertIn("непонятный формат", str(caught.exception))


class InstallFromArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_nested_root_folder_is_flattened(self) -> None:
        archive = make_fake_archive(self.tmp / "cache" / "Reduced_CSDK_12.zip")
        install = self.tmp / "install" / "Reduced_CSDK_12"
        compiler = csdk_setup.install_from_archive(archive, install)
        self.assertIsNotNone(compiler)
        assert compiler is not None
        self.assertEqual(install / COMPILER_RELATIVE, compiler)
        self.assertTrue((install / "csdkcfg.exe").is_file())
        self.assertTrue((install / "game" / "citadel" / "gameinfo.gi").is_file())
        # Лишний корневой каталог должен исчезнуть.
        self.assertFalse((install / "Reduced_CSDK_12").exists())
        # Временная папка распаковки не должна остаться.
        self.assertFalse(Path(str(install) + ".unpacking").exists())

    def test_already_flat_archive_is_accepted(self) -> None:
        archive = make_fake_archive(self.tmp / "flat.zip", nested=False)
        install = self.tmp / "flat-install"
        compiler = csdk_setup.install_from_archive(archive, install)
        self.assertEqual(install / COMPILER_RELATIVE, compiler)

    def test_existing_installation_is_replaced(self) -> None:
        archive = make_fake_archive(self.tmp / "new.zip")
        install = self.tmp / "install"
        install.mkdir(parents=True)
        (install / "старый-мусор.txt").write_text("old", encoding="utf-8")
        compiler = csdk_setup.install_from_archive(archive, install)
        self.assertIsNotNone(compiler)
        self.assertFalse((install / "старый-мусор.txt").exists())
        self.assertTrue((install / COMPILER_RELATIVE).is_file())

    def test_archive_without_compiler_is_an_error(self) -> None:
        archive = self.tmp / "wrong.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("readme.txt", "this is not the CSDK you are looking for")
        with self.assertRaises(csdk_setup.CsdkError) as caught:
            csdk_setup.install_from_archive(archive, self.tmp / "install")
        self.assertIn("resourcecompiler", str(caught.exception))

    def test_zip_slip_is_rejected(self) -> None:
        archive = self.tmp / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("../../evil.txt", "gotcha")
            zipped.writestr(str(COMPILER_RELATIVE), "ok")
        with self.assertRaises(csdk_setup.CsdkError) as caught:
            csdk_setup.install_from_archive(archive, self.tmp / "install")
        self.assertIn("опасный путь", str(caught.exception))
        self.assertFalse((self.tmp.parent / "evil.txt").exists())


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.server = LocalServer()
        self.addCleanup(self.server.stop)
        # Настоящий CSDK весит гигабайты, но в тестах порог не важен.
        patcher = mock.patch.object(csdk_setup, "MIN_ARCHIVE_BYTES", 16)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _server_url(self) -> str:
        return self.server.url

    def test_plain_download_uses_name_from_headers(self) -> None:
        with TemporaryDirectory() as tmp:
            source = make_fake_archive(Path(tmp) / "src.zip")
            self.server.handler.payload = source.read_bytes()
            target = csdk_setup.download(self._server_url(), self.tmp / "cache")
            self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertEqual("Reduced_CSDK_12.zip", target.name)
            # Недокачанного хвоста и его метки после успеха быть не должно.
            self.assertFalse((self.tmp / "cache" / csdk_setup.PART_NAME).exists())

    def test_download_resumes_what_was_already_there(self) -> None:
        with TemporaryDirectory() as tmp:
            source = make_fake_archive(Path(tmp) / "src.zip")
            payload = source.read_bytes()
            self.server.handler.payload = payload
            cache = self.tmp / "cache"
            cache.mkdir(parents=True)
            # Имитируем обрыв: первая половина уже лежит на диске.
            (cache / csdk_setup.PART_NAME).write_bytes(payload[: len(payload) // 2])
            (cache / (csdk_setup.PART_NAME + ".url")).write_text(self._server_url(), encoding="utf-8")
            target = csdk_setup.download(self._server_url(), cache)
            self.assertEqual(payload, target.read_bytes())
            # Догрузка: серверу ушёл запрос хвоста с того самого места.
            self.assertEqual([f"bytes={len(payload) // 2}-"],
                             [item for item in self.server.handler.ranges if item])

    def test_confirm_page_is_passed_automatically(self) -> None:
        with TemporaryDirectory() as tmp:
            source = make_fake_archive(Path(tmp) / "src.zip")
            self.server.handler.payload = source.read_bytes()
            self.server.handler.html = (
                "<html><body>Google Drive can't scan this file for viruses."
                '<form action="/uc" method="get">'
                '<input type="hidden" name="confirm" value="t0k3n">'
                '<input type="hidden" name="uuid" value="u1">'
                "</form></body></html>"
            )
            self.server.handler.html_times = 1
            target = csdk_setup.download(self._server_url(), self.tmp / "cache")
            self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertIn("confirm=t0k3n", self.server.handler.requests[-1])
            self.assertIn("uuid=u1", self.server.handler.requests[-1])

    def test_confirm_page_loop_is_a_clear_error(self) -> None:
        self.server.handler.payload = b"PK\x03\x04" + b"\0" * 64
        self.server.handler.html = (
            '<html><form><input type="hidden" name="confirm" value="t0k3n"></form></html>'
        )
        self.server.handler.html_times = 9  # сервер упорно отвечает страницей
        with self.assertRaises(csdk_setup.CsdkError) as caught:
            csdk_setup.download(self._server_url(), self.tmp / "cache", attempts=4)
        self.assertIn("подтверждения", str(caught.exception))

    def test_sha256_mismatch_is_an_error(self) -> None:
        with TemporaryDirectory() as tmp:
            source = make_fake_archive(Path(tmp) / "src.zip")
            self.server.handler.payload = source.read_bytes()
            with self.assertRaises(csdk_setup.CsdkError) as caught:
                csdk_setup.download(self._server_url(), self.tmp / "cache", sha256="00" * 32)
            self.assertIn("контрольная сумма", str(caught.exception))

    def test_suspiciously_small_file_is_an_error(self) -> None:
        self.server.handler.payload = b"error" * 32
        with mock.patch.object(csdk_setup, "MIN_ARCHIVE_BYTES", 1 << 20):
            with self.assertRaises(csdk_setup.CsdkError) as caught:
                csdk_setup.download(self._server_url(), self.tmp / "cache")
        self.assertIn("подозрительно мал", str(caught.exception))


class SevenZipTest(unittest.TestCase):
    """.7z/.rar распаковываются внешним 7-Zip: нет его — должна быть понятная ошибка."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_7z_without_the_tool_is_an_understandable_error(self) -> None:
        archive = self.tmp / "Reduced_CSDK_12.7z"
        archive.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 64)
        with mock.patch.object(csdk_setup, "find_seven_zip", return_value=None), \
                mock.patch.object(csdk_setup, "install_seven_zip", return_value=None):
            with self.assertRaises(csdk_setup.CsdkError) as caught:
                csdk_setup.install_from_archive(archive, self.tmp / "install", log=_quiet)
        self.assertIn("7-Zip", str(caught.exception))

    def test_7z_is_unpacked_by_the_tool_when_it_exists(self) -> None:
        import subprocess

        archive = self.tmp / "Reduced_CSDK_12.7z"
        archive.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 64)
        destination = self.tmp / "install"

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            # «7-Zip» честно создаёт ожидаемое дерево CSDK
            target = Path(command[3][2:]) / COMPILER_RELATIVE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"MZ")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(csdk_setup, "find_seven_zip", return_value=Path("7z")), \
                mock.patch.object(csdk_setup.subprocess, "run", fake_run):
            compiler = csdk_setup.install_from_archive(archive, destination, log=_quiet)
        self.assertEqual(destination / COMPILER_RELATIVE, compiler)


class EnsureCsdkTest(unittest.TestCase):
    """Поиск + скачивание как единый сценарий.

    Поиск подменяем: тест не должен зависеть от того, стоит ли CSDK на машине
    разработчика. Подмена честно проверяет явный ``--csdk`` и «не находит»
    ничего в остальных случаях.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @contextlib.contextmanager
    def _search_only_explicit(self) -> object:
        def fake_find(explicit: object = None) -> Path | None:
            if explicit is not None and paths.csdk_compiler(Path(explicit)) is not None:
                return Path(explicit)
            return None

        with mock.patch.object(csdk_setup.paths_mod, "find_csdk", fake_find):
            yield

    @contextlib.contextmanager
    def _nothing_installed(self) -> object:
        with mock.patch.object(csdk_setup.paths_mod, "find_csdk", lambda *a, **k: None):
            yield

    def test_already_installed_is_found_without_downloading(self) -> None:
        installed = self.tmp / "CSDK"
        (installed / COMPILER_RELATIVE.parent).mkdir(parents=True)
        (installed / COMPILER_RELATIVE).write_bytes(b"MZ")

        def boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("скачивание не должно запускаться")

        with self._search_only_explicit(), mock.patch.object(csdk_setup, "download", boom):
            result = csdk_setup.ensure_csdk(explicit=installed, log=_quiet)
        self.assertEqual("found", result.status)
        self.assertTrue(result.ok)
        self.assertEqual(installed / COMPILER_RELATIVE, result.compiler)

    def test_download_disabled_reports_status(self) -> None:
        with self._nothing_installed():
            result = csdk_setup.ensure_csdk(allow_download=False, log=_quiet)
        self.assertEqual("disabled", result.status)
        self.assertFalse(result.ok)
        self.assertTrue(any(csdk_setup.CSDK_PAGE_URL in message for message in result.messages),
                        result.messages)

    def test_missing_csdk_is_downloaded_and_unpacked(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = make_fake_archive(Path(tmp) / "Reduced_CSDK_12.zip")
            install = self.tmp / "install" / "Reduced_CSDK_12"

            def fake_download(url: str, directory: Path, **kwargs: object) -> Path:
                target = Path(directory) / archive.name
                Path(directory).mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read_bytes())
                return target

            with self._nothing_installed(), mock.patch.object(csdk_setup, "download", fake_download):
                result = csdk_setup.ensure_csdk(install_root=install, cache_dir=self.tmp / "cache", log=_quiet)

        self.assertEqual("downloaded", result.status, result.messages)
        self.assertTrue(result.ok)
        self.assertEqual(install / COMPILER_RELATIVE, result.compiler)
        self.assertIsNotNone(paths.csdk_compiler(install))

    def test_download_error_is_returned_not_raised(self) -> None:
        with self._nothing_installed():
            with mock.patch.object(csdk_setup, "download",
                                   side_effect=csdk_setup.CsdkError("сеть недоступна")):
                result = csdk_setup.ensure_csdk(install_root=self.tmp / "install", log=_quiet)
        self.assertEqual("error", result.status)
        self.assertFalse(result.ok)
        self.assertIn("сеть недоступна", result.messages[0])

    def test_unsafe_install_dir_is_refused_before_download(self) -> None:
        with self._nothing_installed():
            with self.assertRaises(csdk_setup.CsdkError) as caught:
                csdk_setup.ensure_csdk(install_root=r"C:\Users\me\OneDrive\csdk", log=_quiet)
        self.assertIn("OneDrive", str(caught.exception))

    def test_url_from_environment_is_used(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = make_fake_archive(Path(tmp) / "Reduced_CSDK_12.zip")
            seen: dict[str, str] = {}

            def fake_download(url: str, directory: Path, **kwargs: object) -> Path:
                seen["url"] = url
                target = Path(directory) / archive.name
                Path(directory).mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read_bytes())
                return target

            with self._nothing_installed(), \
                    mock.patch.dict("os.environ", {"DEADLOCK_CSDK_URL": "https://example.invalid/csdk.zip"}):
                with mock.patch.object(csdk_setup, "download", fake_download):
                    csdk_setup.ensure_csdk(install_root=self.tmp / "install", cache_dir=self.tmp / "cache")
        self.assertEqual("https://example.invalid/csdk.zip", seen["url"])

    def test_second_run_reuses_the_same_install_dir(self) -> None:
        """Повторная сборка не должна заново разворачивать архив."""
        install = self.tmp / "install"
        (install / COMPILER_RELATIVE.parent).mkdir(parents=True)
        (install / COMPILER_RELATIVE).write_bytes(b"MZ")

        def boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("CSDK уже развёрнут — скачивание не нужно")

        with self._nothing_installed(), mock.patch.object(csdk_setup, "download", boom):
            result = csdk_setup.ensure_csdk(install_root=install, log=_quiet)
        self.assertEqual("found", result.status)
        self.assertEqual(install / COMPILER_RELATIVE, result.compiler)

    def test_cached_archive_is_not_downloaded_again(self) -> None:
        cache = self.tmp / "cache"
        archive = make_fake_archive(cache / "Reduced_CSDK_12.zip")

        def boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("архив уже в кэше — качать не надо")

        with self._nothing_installed(), mock.patch.object(csdk_setup, "download", boom):
            # Настоящий архив весит гигабайты; в тесте порог sanity-проверки ниже.
            with mock.patch.object(csdk_setup, "MIN_ARCHIVE_BYTES", 16):
                result = csdk_setup.ensure_csdk(install_root=self.tmp / "install", cache_dir=cache)
        self.assertEqual("downloaded", result.status)
        self.assertEqual(archive, result.archive)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = csdk_setup.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_found_installation_prints_root_and_writes_file(self) -> None:
        installed = self.tmp / "CSDK"
        (installed / COMPILER_RELATIVE.parent).mkdir(parents=True)
        (installed / COMPILER_RELATIVE).write_bytes(b"MZ")
        root_file = self.tmp / "root.txt"
        code, out, err = self._run(["--csdk", str(installed), "--root-file", str(root_file)])
        self.assertEqual(csdk_setup.EXIT_OK, code, out + err)
        self.assertIn("CSDK_ROOT=", out)
        self.assertEqual(str(installed), root_file.read_text(encoding="utf-8").strip())

    def test_json_output_is_parsable(self) -> None:
        installed = self.tmp / "CSDK"
        (installed / COMPILER_RELATIVE.parent).mkdir(parents=True)
        (installed / COMPILER_RELATIVE).write_bytes(b"MZ")
        code, out, _err = self._run(["--csdk", str(installed), "--json"])
        self.assertEqual(csdk_setup.EXIT_OK, code, out)
        payload = json.loads(out)
        self.assertEqual("found", payload["status"])
        self.assertEqual(str(installed / COMPILER_RELATIVE), payload["compiler"])

    def test_no_download_flag_reports_missing(self) -> None:
        with mock.patch.object(csdk_setup.paths_mod, "find_csdk", lambda *a, **k: None):
            code, out, _err = self._run(["--no-download"])
        self.assertEqual(csdk_setup.EXIT_MISSING, code, out)

    def test_cli_as_subprocess_does_not_crash(self) -> None:
        installed = self.tmp / "CSDK"
        (installed / COMPILER_RELATIVE.parent).mkdir(parents=True)
        (installed / COMPILER_RELATIVE).write_bytes(b"MZ")
        root_file = self.tmp / "root.txt"
        done = run_cli(
            _support.TOOLS_DIR / "csdk_setup.py",
            ["--csdk", str(installed), "--root-file", str(root_file)],
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(str(installed), root_file.read_text(encoding="utf-8").strip())


class BuildIntegrationTest(unittest.TestCase):
    """Сборщик должен подхватить CSDK, установленный автоматически."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _ensure_quiet(self, result: "csdk_setup.SetupResult") -> mock.Mock:
        """Прогнать шаг 0 сборки и вернуть подмену ensure_csdk (без шума в выводе)."""
        import build_mod

        sentinel = mock.Mock(return_value=result)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(build_mod.csdk_setup, "ensure_csdk", sentinel):
            build_mod._ensure_csdk(self.ctx)
        return sentinel

    def _ctx(self, argv: list[str]):
        import build_mod

        parser = build_mod.build_parser()
        args = parser.parse_args(argv)
        return build_mod.Context(
            root=_support.MOD_ROOT, out_dir=self.tmp / "dist", build_dir=self.tmp / "build",
            addon_name="bhop_trainer", version="1.0.0", args=args,
        ), args

    def test_setup_result_becomes_args_csdk(self) -> None:
        import build_mod

        installed = self.tmp / "CSDK"
        compiler = installed / COMPILER_RELATIVE
        compiler.parent.mkdir(parents=True)
        compiler.write_bytes(b"MZ")
        result = csdk_setup.SetupResult(
            root=installed, compiler=compiler, status="downloaded", messages=["CSDK установлен"]
        )
        ctx, args = self._ctx(["--hud-xml", str(_support.HUD_FIXTURE)])
        self.ctx = ctx
        mocked = self._ensure_quiet(result)
        self.assertTrue(mocked.called)
        self.assertEqual(str(installed), args.csdk)
        self.assertEqual("downloaded", ctx.state["csdk_status"])
        self.assertIn("CSDK установлен", ctx.notes)

    def test_failed_setup_becomes_a_warning(self) -> None:
        import build_mod

        result = csdk_setup.SetupResult(status="error", messages=["сеть недоступна"])
        ctx, args = self._ctx(["--hud-xml", str(_support.HUD_FIXTURE)])
        self.ctx = ctx
        self._ensure_quiet(result)
        self.assertIsNone(args.csdk)
        self.assertEqual("error", ctx.state["csdk_status"])
        self.assertIn("сеть недоступна", ctx.warnings)

    def test_no_setup_when_compiler_is_substituted(self) -> None:
        import build_mod

        ctx, _args = self._ctx(["--hud-xml", str(_support.HUD_FIXTURE), "--compiler-cmd", "echo {source}"])
        with mock.patch.object(build_mod.csdk_setup, "ensure_csdk") as mocked:
            build_mod._ensure_csdk(ctx)
        self.assertFalse(mocked.called)

    def test_no_setup_for_sources_only_build(self) -> None:
        import build_mod

        ctx, _args = self._ctx(["--hud-xml", str(_support.HUD_FIXTURE), "--allow-uncompiled"])
        with mock.patch.object(build_mod.csdk_setup, "ensure_csdk") as mocked:
            build_mod._ensure_csdk(ctx)
        self.assertFalse(mocked.called)

    def test_clean_flag_removes_working_dirs(self) -> None:
        import build_mod

        out_dir = self.tmp / "dist"
        build_dir = self.tmp / "build"
        out_dir.mkdir()
        build_dir.mkdir()
        (out_dir / "old.vpk").write_bytes(b"old")
        (build_dir / "old.txt").write_text("old", encoding="utf-8")
        code, output = self._run([
            "--hud-xml", str(_support.HUD_FIXTURE), "--compiler-cmd", _support.COMPILER_CMD_TEMPLATE,
            "--out", str(out_dir), "--build-dir", str(build_dir), "--clean",
        ])
        self.assertEqual(build_mod.EXIT_OK, code, output)
        self.assertFalse((build_dir / "old.txt").exists())
        self.assertTrue((out_dir / "bhop_trainer_dir.vpk").is_file())

    def _run(self, argv: list[str]) -> tuple[int, str]:
        import build_mod

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = build_mod.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_report_mentions_the_compiler(self) -> None:
        """Отчёт честно показывает, чем именно компилировалось."""
        import build_mod

        installed = self.tmp / "CSDK"
        compiler = installed / COMPILER_RELATIVE
        compiler.parent.mkdir(parents=True)
        compiler.write_bytes(b"MZ")
        result = csdk_setup.SetupResult(root=installed, compiler=compiler, status="downloaded")
        ctx, _args = self._ctx(["--hud-xml", str(_support.HUD_FIXTURE)])
        self.ctx = ctx
        self._ensure_quiet(result)
        report = build_mod._render_report(ctx)
        self.assertIn("resourcecompiler.exe", report)
        self.assertIn("downloaded", report)
        self.assertIn(str(installed), report)


if __name__ == "__main__":
    unittest.main()
