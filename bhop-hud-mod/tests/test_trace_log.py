"""Журнал сборки: консоль + файл, команды, вывод, решения, итоговый блок.

Смысл этих тестов — в том, что журнал и есть средство разбора: по нему чужую
поломку видно целиком. Поэтому проверяется не «функция вызвалась», а что в файле
действительно оказывается всё: окружение, команда с аргументами, полный вывод
процесса, трассировка исключения, предупреждения и итог с путём к самому файлу.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _support  # noqa: F401 - добавляет tools/ в sys.path
from _support import PYTHON

import trace_log


class JournalCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.log_path = self.tmp / "run.log"
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(trace_log.reset)  # после теста процесс снова без журнала

    def capture(self, *, to_stderr: bool = False):
        """Подменить консоль строковыми буферами (как это делает redirect_stdout)."""
        out, err = io.StringIO(), io.StringIO()
        context = contextlib.ExitStack()
        context.enter_context(contextlib.redirect_stdout(out))
        context.enter_context(contextlib.redirect_stderr(err))
        return context, out, err

    def read(self) -> str:
        return self.log_path.read_text(encoding="utf-8")


class SilenceTest(JournalCase):
    """Пока журнал не настроен, библиотека молчит про мелочи."""

    def test_detail_and_trace_are_silent_until_configured(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            trace_log.detail("мелкая подробность")
            trace_log.trace("совсем мелкая")
            trace_log.info("важное сообщение")
        text = out.getvalue()
        self.assertNotIn("мелкая подробность", text)
        self.assertNotIn("совсем мелкая", text)
        self.assertIn("важное сообщение", text)

    def test_configured_journal_shows_detail_and_trace(self) -> None:
        context, out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path, trace=True)
            trace_log.detail("мелкая подробность")
            trace_log.trace("совсем мелкая")
        text = out.getvalue()
        self.assertIn("мелкая подробность", text)
        self.assertIn("совсем мелкая", text)


class TeeTest(JournalCase):
    """В файл попадает всё, что печатает процесс, а не только строки журнала."""

    def test_file_gets_journal_lines_and_other_modules_prints(self) -> None:
        context, out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path)
            trace_log.info("строка журнала")
            print("обычный print из соседнего модуля")  # noqa: T201 - проверяем именно print
            sys.stderr.write("запись в stderr\n")
        text = self.read()
        for needle in ("строка журнала", "обычный print из соседнего модуля", "запись в stderr"):
            self.assertIn(needle, text)
        self.assertIn("обычный print из соседнего модуля", out.getvalue())

    def test_file_is_utf8_crlf_without_bom(self) -> None:
        context, _out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path)
            trace_log.info("кириллица: проверка")
        raw = self.log_path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM не нужен: он мешает findstr")
        self.assertIn("кириллица: проверка".encode("utf-8"), raw)
        self.assertEqual(0, raw.count(b"\n") - raw.count(b"\r\n"), "каждая строка должна кончаться CRLF")

    def test_shutdown_writes_latest_copy_and_reports_the_path(self) -> None:
        context, out, _err = self.capture()
        with context:
            log = trace_log.configure(log_path=self.log_path)
            trace_log.info("что-то важное")
            self.assertEqual(str(self.log_path), str(log.log_path))
            trace_log.shutdown()
        latest = self.log_path.with_name("latest.log")
        self.assertTrue(latest.is_file())
        self.assertEqual(self.read(), latest.read_text(encoding="utf-8"))
        self.assertIn("что-то важное", out.getvalue())

    def test_quiet_mode_keeps_warnings_but_drops_details(self) -> None:
        context, _out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path, verbose=False)
            trace_log.detail("подробность")
            trace_log.warn("предупреждение")
        text = self.read()
        self.assertNotIn("подробность", text)
        self.assertIn("предупреждение", text)


class CommandTest(JournalCase):
    """Внешние команды: с чем запускались, сколько работали, что напечатали."""

    def test_command_arguments_result_and_full_output_are_logged(self) -> None:
        script = "import sys; [print('строка', i) for i in range(3)]; sys.stderr.write('ошибка в stderr' + chr(10))"
        context, _out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path)
            result = trace_log.run([PYTHON, "-c", script], note="проверка вывода")
        self.assertEqual(0, result.returncode)
        text = self.read()
        self.assertIn("ЗАПУСК: проверка вывода", text)
        self.assertIn("argv:", text)
        # команда печатается в двух видах: чтобы вставить в cmd.exe и чтобы в bash
        self.assertIn("cmd (Windows", text)
        self.assertIn("cmd (bash)", text)
        self.assertIn("ИТОГ: код 0", text)
        self.assertIn("строка 2", text)  # вывод напечатан целиком
        self.assertIn("ошибка в stderr", text)
        self.assertIn("stderr: 1 строк", text)

    def test_failed_command_lists_the_program_directory(self) -> None:
        """Пропавший ``modtools.dll`` ищут именно рядом с программой."""
        program = self.tmp / "bin" / "resourcecompiler.exe"
        program.parent.mkdir(parents=True)
        program.write_bytes(b"stub")
        (program.parent / "modtools.dll").write_bytes(b"module")

        class Fake:
            returncode = 1
            stdout = "CAppSystemDict:Unable to load module modtools (Dependency of application), error 126"
            stderr = ""

        with contextlib.ExitStack() as stack:
            stack.enter_context(self.capture()[0])
            trace_log.configure(log_path=self.log_path)
            with unittest.mock.patch.object(trace_log.subprocess, "run", return_value=Fake()):
                trace_log.run([str(program), "-nop4"], note="падение компилятора")
        text = self.read()
        self.assertIn("Unable to load module modtools", text)
        self.assertIn("modtools.dll", text)
        self.assertIn("ИТОГ: код 1", text)

    def test_unwritable_log_file_warns_but_does_not_break_the_run(self) -> None:
        blocker = self.tmp / "not-a-directory"
        blocker.write_text("занято", encoding="utf-8")
        context, out, _err = self.capture()
        with context:
            log = trace_log.configure(log_path=blocker / "logs" / "run.log")
            trace_log.info("сборка продолжается")
            trace_log.shutdown()
        self.assertIsNone(log.log_path)
        self.assertIn("не удалось открыть файл журнала", out.getvalue())
        self.assertIn("сборка продолжается", out.getvalue())


class SummaryTest(JournalCase):
    def test_summary_lists_status_problems_and_artifacts(self) -> None:
        artifact = self.tmp / "bhop_trainer_dir.vpk"
        artifact.write_bytes(b"vpk" * 10)
        context, out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path)
            trace_log.warn("первое предупреждение")
            trace_log.error("поломка")
            trace_log.summary(status="ошибка сборки", artifacts=[artifact])
            trace_log.shutdown()
        text = self.read()
        self.assertIn("ИТОГ", text)
        self.assertIn("статус: ошибка сборки", text)
        self.assertIn("предупреждений: 1", text)
        self.assertIn("ошибок: 1", text)
        self.assertIn("первое предупреждение", text)
        self.assertIn("bhop_trainer_dir.vpk", text)
        self.assertIn("sha256", text)
        self.assertIn("Журнал целиком", text)
        self.assertIn("Журнал целиком", out.getvalue())

    def test_exception_is_logged_with_a_traceback(self) -> None:
        context, _out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path)
            try:
                raise RuntimeError("компилятор не найден")
            except RuntimeError as error:
                trace_log.exception(error, where="шаг compile")
        text = self.read()
        self.assertIn("ИСКЛЮЧЕНИЕ в шаг compile: RuntimeError: компилятор не найден", text)
        self.assertIn("Traceback (most recent call last)", text)


class EncodingRepairTest(JournalCase):
    """``cmd.exe`` дописывает свои строки в OEM-кодировке — файл должен остаться читаемым."""

    def test_repair_recovers_oem_lines_and_keeps_utf8_ones(self) -> None:
        path = self.tmp / "mixed.log"
        utf8_line = "[i] кириллица из python: C:\\Users\\Пользователь\\csdk"
        oem_line = "[launcher] csdk: C:\\Users\\Пользователь\\csdk"
        path.write_bytes(utf8_line.encode("utf-8") + b"\r\n" + oem_line.encode("cp866") + b"\r\n")
        fixed = trace_log.repair_mixed_encoding(path)
        self.assertEqual(1, fixed)
        text = path.read_text(encoding="utf-8")
        self.assertIn(utf8_line, text)
        self.assertIn(oem_line, text)

    def test_clean_utf8_file_is_left_alone(self) -> None:
        path = self.tmp / "clean.log"
        path.write_text("обычный журнал\nвторая строка\n", encoding="utf-8")
        before = path.read_bytes()
        self.assertEqual(0, trace_log.repair_mixed_encoding(path))
        self.assertEqual(before, path.read_bytes())

    def test_cli_repair_is_quiet_and_idempotent(self) -> None:
        path = self.tmp / "cli.log"
        path.write_bytes("[launcher] путь: C:\\Пользователь\n".encode("cp866"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            first = trace_log.main(["--repair", str(path)])
            second = trace_log.main(["--repair", str(path)])
        self.assertEqual(0, first)
        self.assertEqual(0, second)
        self.assertIn("log repaired", out.getvalue())
        self.assertIn("Пользователь", path.read_text(encoding="utf-8"))


class SnapshotTest(JournalCase):
    def test_snapshot_records_environment_and_extra_facts(self) -> None:
        context, _out, _err = self.capture()
        with context:
            log = trace_log.configure(log_path=self.log_path, trace=True)
            log.snapshot(extra={"каталог мода": self.tmp})
        text = self.read()
        for needle in ("Окружение", "система:", "python:", "рабочий каталог", "диск", "каталог мода"):
            self.assertIn(needle, text)
        self.assertIn(str(self.tmp), text)
        # в trace-режиме видно и переменные окружения построчно
        self.assertIn("PATH[0]", text)

    def test_problems_are_counted_by_level(self) -> None:
        context, _out, _err = self.capture()
        with context:
            log = trace_log.configure(log_path=self.log_path)
            trace_log.warn("одно")
            trace_log.warn("два")
            trace_log.error("беда")
            log.summary(status="проверка счётчиков")
        text = self.read()
        self.assertIn("предупреждений: 2", text)
        self.assertIn("ошибок: 1", text)

    def test_json_logging_goes_to_stderr_so_stdout_stays_json(self) -> None:
        """С ``--json`` люди читают stderr, а stdout обязан остаться разбираемым."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            trace_log.configure(log_path=self.log_path, to_stderr=True)
            trace_log.info("человеческая строка")
            print(json.dumps({"status": "ok"}))
            trace_log.shutdown()
        self.assertEqual({"status": "ok"}, json.loads(out.getvalue()))
        self.assertIn("человеческая строка", err.getvalue())
        self.assertIn("человеческая строка", self.read())


class CliTest(JournalCase):
    """``--log-file``/``--log-dir``/``--no-log-file``/``--quiet``/``--trace``."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="tool")
        trace_log.patch_cli(parser)
        return parser

    def test_flags_are_declared(self) -> None:
        help_text = self._parser().format_help()
        for flag in ("--log-file", "--log-dir", "--no-log-file", "--quiet", "--trace"):
            self.assertIn(flag, help_text)

    def test_log_file_comes_from_the_flag(self) -> None:
        args = self._parser().parse_args(["--log-file", str(self.log_path), "--trace"])
        self.assertEqual(self.log_path, trace_log.log_path_for(args, default_dir=self.tmp / "logs"))
        with contextlib.redirect_stdout(io.StringIO()):
            log = trace_log.apply_cli(args, default_dir=self.tmp / "logs", header="проверка")
        self.assertTrue(self.log_path.is_file())
        self.assertTrue(log.trace_enabled)

    def test_log_dir_and_prefix_make_a_stamped_name(self) -> None:
        logs = self.tmp / "logs"
        args = self._parser().parse_args(["--log-dir", str(logs)])
        path = trace_log.log_path_for(args, default_dir=self.tmp / "other", prefix="csdk")
        self.assertEqual(logs, path.parent)
        self.assertTrue(path.name.startswith("csdk-"))
        self.assertTrue(path.name.endswith(".log"))

    def test_no_log_file_means_console_only(self) -> None:
        args = self._parser().parse_args(["--no-log-file"])
        self.assertIsNone(trace_log.log_path_for(args, default_dir=self.tmp / "logs"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            trace_log.apply_cli(args, default_dir=self.tmp / "logs", header="в консоль")
            trace_log.info("и только в консоль")
        self.assertIn("и только в консоль", out.getvalue())
        self.assertFalse((self.tmp / "logs").exists())

    def test_quiet_and_trace_reach_the_journal(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            quiet = trace_log.apply_cli(self._parser().parse_args(["--quiet", "--log-file", str(self.log_path)]),
                                        default_dir=self.tmp / "logs")
            self.assertFalse(quiet.verbose)
            self.assertFalse(quiet.trace_enabled)
            trace_log.reset()
            traced = trace_log.apply_cli(self._parser().parse_args(["--trace", "--log-file", str(self.log_path)]),
                                         default_dir=self.tmp / "logs")
        self.assertTrue(traced.verbose)
        self.assertTrue(traced.trace_enabled)


class ReconfigureTest(JournalCase):
    def test_second_configure_switches_the_file_and_keeps_streams_intact(self) -> None:
        second = self.tmp / "second.log"
        context, out, _err = self.capture()
        with context:
            trace_log.configure(log_path=self.log_path)
            print("в первый файл")
            trace_log.configure(log_path=second)
            print("во второй файл")
            trace_log.shutdown()
        self.assertIn("в первый файл", self.read())
        self.assertNotIn("во второй файл", self.read())
        self.assertIn("во второй файл", second.read_text(encoding="utf-8"))
        # консоль не потерялась: подмена потоков снимается и ставится заново
        self.assertIn("в первый файл", out.getvalue())
        self.assertIn("во второй файл", out.getvalue())

    def test_reset_and_shutdown_are_harmless_without_a_journal(self) -> None:
        trace_log.reset()
        trace_log.shutdown()
        trace_log.reset()


if __name__ == "__main__":
    unittest.main()
