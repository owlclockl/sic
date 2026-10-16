#!/usr/bin/env python3
"""Подробный журнал сборки: консоль + файл, тайминги, команды, вывод, решения.

Зачем это нужно: когда сборка падает на чужой машине, единственный источник
правды — её вывод. Поэтому журнал пишет всё, что можно записать:

* окружение (ОС, Python, разряды, кодировки, свободное место, ключевые
  переменные, пути Steam/Deadlock/CSDK);
* каждое решение — что выбрано и **почему**, а также что было отброшено;
* каждую внешнюю команду целиком: путь, аргументы, рабочий каталог, кто её
  запустил, сколько она работала, что напечатала (полностью, без обрезки);
* каждый значимый файл: размер, время, ``sha256``;
* содержимое каталогов, в которых что-то пошло не так (в первую очередь
  рядом с ``resourcecompiler.exe`` — там ищется пропавший ``modtools``);
* все предупреждения и ошибки — и в конце готовый блок «что отправить».

Консоль и файл получают **один и тот же** текст: журнал подменяет
``sys.stdout``/``sys.stderr`` на поток-«тройник», так что в файл попадает даже
то, что печатают чужие куски кода (в том числе вывод ``print`` из соседних
модулей и запись в ``stderr``). Файл — обычный текст в UTF-8 с CRLF, его можно
приложить к письму или просто скопировать консоль.

Уровни (по возрастанию важности): ``trace`` < ``detail`` < ``info`` < ``ok``
< ``warn`` < ``error``.

* ``detail`` — то, что нужно для разбора чужой поломки; видно по умолчанию
  (подробный режим включён, это и просили);
* ``trace`` — совсем мелкое: хеши каждого файла, полные листинги каталогов,
  дамп переменных окружения (включается ``--trace``);
* ``--quiet`` — только ``info`` и выше.

Отдельная мелочь: ``build.bat`` дописывает свои (чисто ASCII) строки в тот же
файл средствами ``cmd``, а его ``echo`` пишет в OEM-кодировке консоли. Если в
пути встретится кириллица, такие строки окажутся не в UTF-8 — поэтому в конце
:func:`repair_mixed_encoding` аккуратно перекодирует файл, ничего не потеряв.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence, TextIO

#: Сколько строк вывода команды печатать «как есть», прежде чем включить
#: аккуратное усечение середины (начала и конца всё равно достаточно для
#: разбора, а гигабайтный лог отправлять нельзя).
MAX_OUTPUT_LINES = 20_000
KEEP_HEAD_LINES = 5_000
KEEP_TAIL_LINES = 5_000

#: Сколько записей каталога печатать, прежде чем свернуть список.
MAX_DIR_ENTRIES = 400

LEVELS: tuple[str, ...] = ("trace", "detail", "info", "ok", "warn", "error")
_LEVEL_INDEX = {level: index for index, level in enumerate(LEVELS)}

#: Короткие метки в начале строки — по ним лог удобно фильтровать.
MARKS = {
    "trace": ".",
    "detail": ">",
    "info": "i",
    "ok": "+",
    "warn": "!",
    "error": "x",
}

_SEPARATOR = "=" * 72


def now_stamp() -> str:
    """Отметка времени для имён файлов: ``20261016-181500``."""
    return time.strftime("%Y%m%d-%H%M%S")


def human_bytes(size: float) -> str:
    """Размер по-человечески: ``1.5 ГиБ``."""
    units = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ")
    value = float(size)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} ТиБ"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    """``sha256`` файла. Ошибки чтения не пробрасываются — это журнал."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError as error:  # нет файла, нет прав, файл занят — не повод падать
        return f"<не прочитан: {error}>"
    return digest.hexdigest()


def describe_file(path: Path) -> str:
    """Одна строка про файл: размер, время и хеш."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError as error:
        return f"{path}: <нет доступа: {error}>"
    stamp = _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"{path}: {human_bytes(stat.st_size)}, изменён {stamp}, sha256 {file_sha256(path)}"


class TeeStream:
    """Поток-«тройник»: всё, что пишут в консоль, дублируется в файл журнала.

    Подменяет ``sys.stdout``/``sys.stderr`` целиком, поэтому в файле оказывается
    и вывод соседних модулей, а не только строки самого журнала.
    """

    def __init__(self, console: TextIO, file: TextIO | None, *, name: str = "out", own_file: bool = False) -> None:
        self.console = console
        self.file = file
        self.name = name
        self.own_file = own_file
        self.closed = False

    # -- io.TextIOBase-подобный минимум ------------------------------------- #

    def write(self, text: str) -> int:
        if self.closed:
            return 0
        written = 0
        try:
            written = self.console.write(text)
        except UnicodeEncodeError:
            # Консоль Windows живёт в cp866/cp1251: символы вроде «·» или «✓»
            # в ней не существуют. Терять из-за этого журнал нельзя.
            encoding = getattr(self.console, "encoding", None) or "utf-8"
            safe = text.encode(encoding, "replace").decode(encoding, "replace")
            try:
                written = self.console.write(safe)
            except Exception:  # noqa: BLE001 - журнал не имеет права падать
                written = 0
        except Exception:  # noqa: BLE001
            written = 0
        if self.file is not None:
            try:
                self.file.write(text)
            except Exception:  # noqa: BLE001
                self.file = None
        return written if written is not None else len(text)

    def flush(self) -> None:
        for stream in (self.console, self.file):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self) -> bool:
        try:
            return bool(self.console.isatty())
        except Exception:  # noqa: BLE001
            return False

    @property
    def encoding(self) -> str:
        return getattr(self.console, "encoding", None) or "utf-8"

    def fileno(self) -> int:
        return self.console.fileno()

    def writable(self) -> bool:
        return True

    def close(self) -> None:
        """Закрыть только собственный файл; консоль остаётся как была."""
        self.flush()
        if self.own_file and self.file is not None:
            try:
                self.file.close()
            except Exception:  # noqa: BLE001
                pass
        self.closed = True


@dataclass
class TraceLog:
    """Журнал одного запуска: строки, проблемы, тайминги, файл."""

    console: TextIO | None = None  # None — брать sys.stdout в момент вызова
    to_stderr: bool = False  # --json: люди читают stderr, stdout остаётся JSON
    verbose: bool = True  # показывать detail (сбрасывается configure(verbose=False))
    trace_enabled: bool = False  # показывать trace
    timestamps: bool = True
    log_path: Path | None = None
    started: float = field(default_factory=time.monotonic)
    area: str = ""
    problems: list[tuple[str, str]] = field(default_factory=list)  # (уровень, текст)
    notes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    stream_name: str = "stdout"
    configured: bool = False
    _file: TextIO | None = None
    _own_file: bool = False
    _previous_stdout: TextIO | None = None
    _previous_stderr: TextIO | None = None
    _patched: bool = False
    _warned_about_file: bool = False

    # -- поток -------------------------------------------------------------- #

    def console_stream(self) -> TextIO:
        if self.console is not None:
            return self.console
        return sys.stderr if self.to_stderr else sys.stdout

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _prefix(self, level: str) -> str:
        mark = MARKS.get(level, "i")
        if not self.timestamps:
            return f"[{mark}] "
        wall = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        return f"{wall} +{self.elapsed():07.3f} [{mark}] "

    def visible(self, level: str) -> bool:
        """Показывать ли строку этого уровня.

        Пока журнал не настроен (``trace_log`` просто импортировали в библиотеке
        или тесте), мелкие уровни молчат: иначе любой вызов ``find_csdk()``
        засорял бы чужой вывод. Как только журнал настроен (``configure()``),
        подробности видны — это и есть обычный режим сборки.
        """
        if level == "trace":
            return self.trace_enabled and self.configured
        if level == "detail":
            return (self.verbose or self.trace_enabled) and self.configured
        return True

    def _emit(self, text: str, level: str) -> None:
        """Строка журнала: префикс, отступ по области, консоль + файл."""
        self.counts[level] = self.counts.get(level, 0) + 1
        if level in ("warn", "error"):
            self.problems.append((level, text))
        if not self.visible(level):
            # В файле «скрытое» тоже не нужно: файл должен совпадать с консолью.
            return
        prefix = self._prefix(level)
        body = text.split("\n")
        out = [prefix + body[0]]
        for extra in body[1:]:
            out.append(f"    {extra}")
        stream = self.console_stream()
        payload = "\n".join(out) + "\n"
        try:
            stream.write(payload)
            stream.flush()
        except Exception:  # noqa: BLE001 - консоль могла умереть (закрыли окно)
            pass

    # -- уровни ------------------------------------------------------------- #

    def log(self, message: str, level: str = "info") -> None:
        """Совместимо с прежним ``build_mod.log()``: та же строка, но с меткой."""
        self._emit(str(message), level)

    def info(self, message: str) -> None:
        self._emit(str(message), "info")

    def detail(self, message: str) -> None:
        self._emit(str(message), "detail")

    def trace(self, message: str) -> None:  # type: ignore[no-redef]
        """Мелкая подробность: видна только с ``--trace``."""
        self._emit(str(message), "trace")

    def ok(self, message: str) -> None:
        self._emit(str(message), "ok")

    def warn(self, message: str) -> None:
        self._emit(str(message), "warn")

    def error(self, message: str) -> None:
        self._emit(str(message), "error")

    # -- структура ---------------------------------------------------------- #

    def rule(self, char: str = "-", width: int = 72) -> None:
        self._emit(char * width, "detail")

    def section(self, title: str) -> None:
        """Крупный заголовок этапа: виден всегда и хорошо ищется в логе."""
        self._emit(_SEPARATOR, "info")
        self._emit(f"  {title}", "info")
        self._emit(_SEPARATOR, "info")

    def step(self, number: str, title: str) -> None:
        self.section(f"[{number}] {title}  (+{self.elapsed():.2f} с)")

    def kv(self, key: str, value: Any, *, level: str = "detail", bullet: str = "·") -> None:
        """Пара «ключ: значение» с выравниванием — так лог читается глазами."""
        self._emit(f"  {bullet} {key}: {value}", level)

    def decision(self, question: str, answer: Any, reason: str = "") -> None:
        """Зафиксировать решение и его причину: «почему выбрано именно это»."""
        self._emit(f"? {question} -> {answer}", "detail")
        if reason:
            self._emit(f"     причина: {reason}", "detail")

    def rejected(self, what: Any, reason: str) -> None:
        """Отброшенный вариант — тоже часть картины."""
        self._emit(f"× {what}: {reason}", "detail")

    def list_dir(self, path: Path, *, note: str = "", limit: int = MAX_DIR_ENTRIES, hashes: bool = False) -> None:
        """Содержимое каталога: имя, размер, время. С хешами — по ``--trace``."""
        path = Path(path)
        self._emit(f"каталог {path}{f' ({note})' if note else ''}:", "detail")
        try:
            entries = sorted(path.iterdir(), key=lambda item: (item.is_dir(), item.name.lower()))
        except OSError as error:
            self._emit(f"    <не прочитан: {error}>", "warn")
            return
        if not entries:
            self._emit("    <пусто>", "detail")
            return
        for entry in entries[:limit]:
            try:
                if entry.is_dir():
                    count = len(list(entry.iterdir()))
                    self._emit(f"    {entry.name}/  — каталог, записей: {count}", "detail")
                    continue
                stat = entry.stat()
                line = f"    {entry.name}  — {human_bytes(stat.st_size)}, {_dt.datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}"
                if hashes or self.trace_enabled:
                    line += f", sha256 {file_sha256(entry)}"
                self._emit(line, "detail")
            except OSError as error:
                self._emit(f"    {entry.name}  — <ошибка: {error}>", "detail")
        if len(entries) > limit:
            self._emit(f"    … всего {len(entries)} записей, показаны первые {limit}", "detail")

    def path_event(self, action: str, path: Path, *, size: int | None = None, sha256: str | None = None,
                   level: str = "detail") -> None:
        """Что случилось с файлом или каталогом: создан, удалён, взят, положен."""
        path = Path(path)
        parts = [f"{action}: {path}"]
        if size is not None:
            parts.append(human_bytes(size))
        if sha256:
            parts.append(f"sha256 {sha256}")
        self._emit("  " + ", ".join(parts), level)

    def env(self, name: str, *, default: str | None = None) -> str:
        """Прочитать переменную окружения и сразу записать факт в журнал."""
        value = os.environ.get(name, default)
        self.kv(name, "<не задана>" if value is None else value)
        return "" if value is None else value

    # -- внешние команды ---------------------------------------------------- #

    def command_line(self, argv: Sequence[str]) -> str:
        """Как команду набрать руками в cmd.exe (для повторения вручную).

        ``list2cmdline`` — та же логика кавычек, которой пользуется ``subprocess``
        на Windows: строка получается ровно такой, какую можно вставить в cmd.
        """
        return subprocess.list2cmdline([str(item) for item in argv])

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        note: str = "",
        list_dir_on_failure: bool = True,
        check: bool = False,
        shell: bool = False,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Запустить внешнюю команду и записать о ней всё, что можно записать.

        Печатаются: полная командная строка (и вариант для копирования в
        cmd.exe), рабочий каталог, переменные окружения (отличия от текущих),
        описание запускаемого файла, код возврата, длительность, полный
        ``stdout`` и ``stderr``. При ошибке — ещё и содержимое каталога с
        программой: именно там видно пропавший ``modtools.dll``.
        """
        argv = [str(item) for item in argv]
        program = Path(argv[0]) if argv else Path("")
        self._emit("", "detail")
        self._emit(f"ЗАПУСК: {note}" if note else "ЗАПУСК", "detail")
        self._emit(f"    argv: {argv}", "detail")
        self._emit(f"    cmd (Windows, можно вставить в cmd.exe): {self.command_line(argv)}", "detail")
        self._emit(f"    cmd (bash): {shlex.join(argv)}", "detail")
        if cwd is not None:
            self._emit(f"    cwd:  {cwd}", "detail")
        self._emit(f"    shell: {shell}, timeout: {timeout if timeout is not None else 'нет'}", "trace")
        if program.name and not shell:
            if program.is_file():
                self._emit("    программа: " + describe_file(program), "detail")
            else:
                self._emit(f"    программа: {program} — файла нет по этому пути", "warn")
        if env is not None:
            added = {key: value for key, value in env.items() if os.environ.get(key) != value}
            self._emit(f"    env (отличия от текущего): {added if added else 'нет'}", "trace")

        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                input=input_text,
                shell=shell,
            )
        except FileNotFoundError as error:
            self._emit(f"    программа не найдена: {error}", "error")
            self.guess_cause(program, "команда не найдена")
            raise
        except subprocess.TimeoutExpired as error:
            spent = time.monotonic() - started
            self._emit(f"    таймаут {timeout} с (ждали {spent:.1f} с) — команда убита", "error")
            out = error.output or ""
            err = error.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            self.subprocess_output(out, err)
            raise
        except OSError as error:
            self._emit(f"    не удалось запустить: {type(error).__name__}: {error}", "error")
            self.guess_cause(program, "команда не запустилась")
            raise

        spent = time.monotonic() - started
        level = "ok" if result.returncode == 0 else "warn"
        self._emit(
            f"ИТОГ: код {result.returncode}, {spent:.3f} с, "
            f"stdout {len(result.stdout or '')} символов, stderr {len(result.stderr or '')} символов",
            level,
        )
        self.subprocess_output(result.stdout or "", result.stderr or "")
        if result.returncode != 0:
            self._emit(f"    команда завершилась с кодом {result.returncode}", "warn")
            if list_dir_on_failure and program.parent.is_dir():
                self.list_dir(program.parent, note="там, где лежит программа, о которой идёт речь")
                self.dll_report(program.parent)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, argv, result.stdout, result.stderr)
        return result

    def subprocess_output(self, stdout: str, stderr: str) -> None:
        """Печатать вывод команды целиком (с аккуратным усечением гигантского)."""
        for name, text in (("stdout", stdout), ("stderr", stderr)):
            if not text:
                self._emit(f"    {name}: <пусто>", "detail")
                continue
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            self._emit(f"    {name}: {len(lines)} строк(и), {len(text)} символов — целиком:", "detail")
            for line in _trim_lines(lines):
                self._emit("    | " + line, "detail")

    def dll_report(self, directory: Path) -> None:
        """Отдельно посчитать DLL рядом с программой: для ``error 126`` это главное."""
        try:
            dlls = sorted(entry.name for entry in Path(directory).iterdir()
                          if entry.is_file() and entry.suffix.lower() in (".dll", ".exe"))
        except OSError as error:
            self._emit(f"    <каталог не прочитан: {error}>", "warn")
            return
        self._emit(f"    модулей (.dll/.exe) в {directory}: {len(dlls)}", "detail")
        for name in dlls:
            self._emit(f"      {name}", "trace" if len(dlls) > 40 else "detail")

    def guess_cause(self, program: Path, what: str) -> None:
        """Короткая подсказка, если команда не запустилась как файл."""
        self._emit(f"    {what}: проверь, что путь существует и это исполняемый файл: {program}", "error")

    # -- ошибки ------------------------------------------------------------- #

    def exception(self, error: BaseException, *, where: str = "") -> None:
        """Полная трассировка исключения — без неё разбор невозможен."""
        title = f"{type(error).__name__}: {error}"
        self._emit(f"ИСКЛЮЧЕНИЕ{f' в {where}' if where else ''}: {title}", "error")
        text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        for line in text.rstrip("\n").split("\n"):
            self._emit("    " + line, "error")
        context = getattr(error, "__context__", None)
        if context is not None:
            self._emit(f"    (было вызвано: {type(context).__name__}: {context})", "detail")

    def problem(self, message: str, *, level: str = "warn") -> None:
        """Запомнить предупреждение/ошибку, чтобы повторить её в итоговом блоке."""
        if level == "warn":
            self.warn(message)
        else:
            self.error(message)

    # -- итог --------------------------------------------------------------- #

    def snapshot(self, *, extra: dict[str, Any] | None = None) -> None:
        """Всё про машину и запуск — то, о чём нельзя переспрашивать."""
        self.section("Окружение")
        bits = platform.uname()
        self.kv("хост", bits.node)
        self.kv("система", f"{bits.system} {bits.release} ({bits.version}), {bits.machine}")
        self.kv("процессор", f"{platform.processor() or 'неизвестен'}, потоков: {os.cpu_count()}")
        self.kv("python", f"{sys.version.split()[0]} ({sys.executable})")
        self.kv("разрядность", f"{64 if sys.maxsize > 2 ** 32 else 32} бит")
        self.kv("кодировки", f"файловая система: {sys.getfilesystemencoding()}, "
                            f"stdout: {getattr(sys.stdout, 'encoding', '?')}, "
                            f"stderr: {getattr(sys.stderr, 'encoding', '?')}")
        self.kv("рабочий каталог", os.getcwd())
        self.kv("аргументы", sys.argv)
        self.kv("журнал", self.log_path if self.log_path else "<без файла>")
        self.kv("уровень подробности",
                "trace (максимум)" if self.trace_enabled else ("подробный" if self.verbose else "краткий"))
        for name in (
            "OS", "PROCESSOR_ARCHITECTURE", "SystemDrive", "SystemRoot", "USERPROFILE",
            "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "ProgramFiles", "ProgramFiles(x86)",
            "OneDrive", "PATH", "COMSPEC", "SESSIONNAME", "USERNAME", "COMPUTERNAME",
            "DEADLOCK_DIR", "DEADLOCK_GAME_DIR", "DEADLOCK_PATH", "DEADLOCK_CSDK", "CSDK_ROOT",
            "CSDK_PATH", "SIC_FAKE_REQUIRE_GAME", "SIC_FAKE_COMPILER_LOG", "STEAM_PATH",
        ):
            if name in os.environ:
                value = os.environ[name]
                if name == "PATH":
                    entries = value.split(os.pathsep)
                    self.kv("PATH", f"{len(entries)} записей; Steam/Python рядом: "
                                    + ", ".join(item for item in entries
                                                if any(word in item.lower() for word in ("steam", "python", "windowsapps"))))
                    for index, item in enumerate(entries):
                        self._emit(f"      PATH[{index}] {item}", "trace")
                else:
                    self.kv(name, value, level="detail")
            else:
                self._emit(f"  · {name}: <не задана>", "trace")
        for drive in _existing_drives():
            try:
                usage = shutil.disk_usage(drive)
                self.kv(f"диск {drive}", f"свободно {human_bytes(usage.free)} из {human_bytes(usage.total)}")
            except OSError as error:
                self.kv(f"диск {drive}", f"<ошибка: {error}>")
        if extra:
            for key, value in extra.items():
                self.kv(key, value)

    def summary(self, *, status: str, artifacts: Iterable[Path] = (), extra: Iterable[str] = (),
                log_path: Path | None = None) -> None:
        """Итог: статус, проблемы, артефакты и инструкция «что отправить»."""
        path = Path(log_path) if log_path else self.log_path
        self.section("ИТОГ")
        self.kv("статус", status, level="info")
        self.kv("время работы", f"{self.elapsed():.2f} с", level="info")
        self.kv("консольных строк", sum(self.counts.values()), level="info")
        warnings = [text for level, text in self.problems if level == "warn"]
        errors = [text for level, text in self.problems if level == "error"]
        self.kv("предупреждений", len(warnings), level="info")
        self.kv("ошибок", len(errors), level="info")
        if warnings:
            self._emit("  Предупреждения:", "info")
            for text in warnings[-30:]:
                self._emit(f"    ! {text.splitlines()[0]}", "info")
            if len(warnings) > 30:
                self._emit(f"    … всего {len(warnings)} (полный список — по тексту выше)", "info")
        if errors:
            self._emit("  Ошибки:", "info")
            for text in errors[-30:]:
                self._emit(f"    x {text.splitlines()[0]}", "info")
            if len(errors) > 30:
                self._emit(f"    … всего {len(errors)} (полный список — по тексту выше)", "info")
        for line in extra:
            self._emit(f"  {line}", "info")
        artifacts = list(artifacts)
        if artifacts:
            self._emit("  Артефакты:", "info")
            for item in artifacts:
                item = Path(item)
                if item.is_dir():
                    self._emit(f"    [каталог] {item}", "info")
                elif item.is_file():
                    self._emit(f"    {item} — {human_bytes(item.stat().st_size)}", "info")
                    self._emit(f"      sha256 {file_sha256(item)}", "info")
                else:
                    self._emit(f"    {item} — нет на диске", "info")
        self._emit("", "info")
        if path:
            self._emit(f"  Журнал целиком: {path}", "info")
            latest = Path(path).with_name("latest.log")
            self._emit(f"  Последний запуск (та же копия): {latest}", "info")
        else:
            self._emit("  Журнал целиком: только консоль (файл отключён --no-log-file).", "info")
            self._emit("  Включить файл: --log-file ФАЙЛ или --log-dir КАТАЛОГ.", "info")
        self._emit("  Что отправить автору сборщика: скопируй весь текст выше (или приложи", "info")
        self._emit("  файл журнала). Секретов в нём нет: только пути, версии и вывод инструментов.", "info")

    # -- файл журнала -------------------------------------------------------- #

    def open_file(self, path: Path) -> bool:
        """Начать писать журнал в файл (UTF-8 + CRLF: так его читают и Блокнот, и я)."""
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8", errors="replace", newline="\r\n", buffering=1)
        except OSError as error:
            if not self._warned_about_file:
                self._warned_about_file = True
                self.warn(f"не удалось открыть файл журнала {path}: {error} — пишу только в консоль")
            return False
        self.close_file()
        self._file = handle
        self._own_file = True
        self.log_path = path
        return True

    def close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:  # noqa: BLE001
                pass
            self._file = None
        self._own_file = False

    def patch_streams(self) -> None:
        """Подменить ``sys.stdout``/``sys.stderr`` «тройником» — чтобы в файл попал весь вывод."""
        if self._patched:
            return
        self._previous_stdout, self._previous_stderr = sys.stdout, sys.stderr
        file = self._file
        sys.stdout = TeeStream(self._previous_stdout, file, name="stdout")  # type: ignore[assignment]
        sys.stderr = TeeStream(self._previous_stderr, file, name="stderr")  # type: ignore[assignment]
        self._patched = True

    def unpatch_streams(self) -> None:
        if not self._patched:
            return
        # Всё, что осталось в буферах «тройников», должно попасть в файл.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
        sys.stdout = self._previous_stdout  # type: ignore[assignment]
        sys.stderr = self._previous_stderr  # type: ignore[assignment]
        self._patched = False

    def copy_latest(self) -> None:
        """Положить рядом ``latest.log`` — стабильное имя, которое легко приложить."""
        if not self.log_path or not Path(self.log_path).is_file():
            return
        target = Path(self.log_path).with_name("latest.log")
        try:
            shutil.copyfile(self.log_path, target)
        except OSError:
            pass

    def close(self) -> None:
        self.unpatch_streams()
        self.copy_latest()
        self.close_file()


# --------------------------------------------------------------------------- #
# модульный уровень: один журнал на процесс
# --------------------------------------------------------------------------- #

_current: TraceLog | None = None


def current() -> TraceLog:
    """Текущий журнал. Если его ещё не настроили — просто пишем в консоль."""
    global _current
    if _current is None:
        _current = TraceLog()
    return _current


def configure(
    *,
    log_path: Path | None = None,
    verbose: bool = True,
    trace: bool = False,
    to_stderr: bool = False,
    timestamps: bool = True,
    console: TextIO | None = None,
    patch_streams: bool = True,
) -> TraceLog:
    """Создать журнал (или дополнить существующий) и начать писать в файл."""
    global _current
    log = _current if _current is not None else TraceLog()
    log.console = console
    log.to_stderr = to_stderr
    # Уровни выставляются явно: --quiet обязан уметь выключить подробности,
    # даже если журнал в этом процессе уже был настроен.
    log.trace_enabled = bool(trace)
    log.verbose = bool(verbose) or bool(trace)
    log.timestamps = timestamps
    log.configured = True
    _current = log
    if log_path is not None:
        was_patched = log._patched
        if was_patched:
            log.unpatch_streams()  # файл сейчас закроется — потоки нельзя оставлять в нём
        if log.open_file(log_path) and (patch_streams or was_patched):
            log.patch_streams()
    return log


def reset() -> None:
    """Забыть текущий журнал (нужно тестам: каждый запуск — чистый журнал)."""
    global _current
    if _current is not None:
        _current.close()
    _current = None


def shutdown() -> TraceLog:
    """Закрыть журнал, снять подмену потоков и обновить ``latest.log``.

    После этого процесса-журнала больше нет: библиотечные вызовы (``vpk``,
    ``paths``) снова молчат, пока кто-нибудь не настроит журнал заново. Так
    подробный вывод остаётся у сборки и не лезет в чужой процесс.
    """
    global _current
    log = current()
    log.close()
    _current = None
    return log


# -- короткие обёртки, чтобы звать журнал откуда угодно --------------------- #

def log(message: str, level: str = "info") -> None:
    current().log(message, level)


def info(message: str) -> None:
    current().info(message)


def detail(message: str) -> None:
    current().detail(message)


def trace(message: str) -> None:  # type: ignore[no-redef]
    current().trace(message)


def ok(message: str) -> None:
    current().ok(message)


def warn(message: str) -> None:
    current().warn(message)


def error(message: str) -> None:
    current().error(message)


def section(title: str) -> None:
    current().section(title)


def kv(key: str, value: Any, *, level: str = "detail") -> None:
    current().kv(key, value, level=level)


def decision(question: str, answer: Any, reason: str = "") -> None:
    current().decision(question, answer, reason)


def rejected(what: Any, reason: str) -> None:
    current().rejected(what, reason)


def list_dir(path: Path, *, note: str = "", limit: int = MAX_DIR_ENTRIES) -> None:
    current().list_dir(path, note=note, limit=limit)


def path_event(action: str, path: Path, *, size: int | None = None, sha256: str | None = None,
               level: str = "detail") -> None:
    current().path_event(action, path, size=size, sha256=sha256, level=level)


def run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return current().run(argv, **kwargs)


def exception(error: BaseException, *, where: str = "") -> None:
    current().exception(error, where=where)


def snapshot(*, extra: dict[str, Any] | None = None) -> None:
    current().snapshot(extra=extra)


def summary(*, status: str, artifacts: Iterable[Path] = (), extra: Iterable[str] = (),
            log_path: Path | None = None) -> None:
    current().summary(status=status, artifacts=artifacts, extra=extra, log_path=log_path)


def describe(path: Path) -> str:
    return describe_file(path)


def _trim_lines(lines: list[str]) -> list[str]:
    """Усечь гигантский вывод: начало и конец важны, середина — нет."""
    if len(lines) <= MAX_OUTPUT_LINES:
        return lines
    head = lines[:KEEP_HEAD_LINES]
    tail = lines[-KEEP_TAIL_LINES:]
    skipped = len(lines) - len(head) - len(tail)
    return head + [f"… здесь пропущено строк: {skipped} (файл с выводом целиком — в build/compile_logs) …"] + tail


def _existing_drives() -> list[str]:
    """Буквы существующих диcков (Windows) — чтобы видеть свободное место."""
    if os.name != "nt":
        return ["/"]
    drives = []
    for letter in "CDEFGHIJK":
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


def patch_cli(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    """Общие для всех инструментов ключи журнала — одним куском.

    ``suppress_defaults`` нужен парсерам с подкомандами: там общий парсер
    подключается и к основному, и к каждой подкоманде, и обычные значения по
    умолчанию подкоманды затирали бы то, что пользователь написал до неё
    (``install_dmm.py --trace stage``). С ``argparse.SUPPRESS`` атрибут не
    выставляется вовсе, и разобранное значение остаётся живым.
    """
    default = argparse.SUPPRESS if suppress_defaults else None
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    group = parser.add_argument_group("журнал")
    group.add_argument(
        "--log-file", metavar="ФАЙЛ", default=default,
        help="файл журнала (по умолчанию — logs\\build-<дата>-<время>.log рядом с артефактами)",
    )
    group.add_argument(
        "--log-dir", metavar="КАТАЛОГ", default=default,
        help="куда положить журнал, если --log-file не задан",
    )
    group.add_argument(
        "--no-log-file", action="store_true", default=flag_default,
        help="не писать файл журнала вовсе",
    )
    group.add_argument(
        "--quiet", action="store_true", default=flag_default,
        help="краткий режим: без мелких подробностей",
    )
    group.add_argument(
        "--trace", action="store_true", default=flag_default,
        help="максимум подробностей: хеши файлов, полные листинги, дамп окружения",
    )


def log_path_for(args: argparse.Namespace, *, default_dir: Path, prefix: str = "build",
                 module: str | None = None) -> Path | None:
    """Куда писать журнал: ``--log-file``, иначе ``--log-dir``/каталог по умолчанию."""
    if getattr(args, "no_log_file", False):
        return None
    explicit = getattr(args, "log_file", None)
    if explicit:
        return Path(explicit).expanduser()
    directory = Path(getattr(args, "log_dir", None) or default_dir)
    name = f"{prefix}-{now_stamp()}.log" if prefix else f"{module or 'log'}-{now_stamp()}.log"
    return directory / name


def apply_cli(args: argparse.Namespace, *, default_dir: Path, prefix: str = "build",
              to_stderr: bool = False, module: str | None = None,
              header: str = "") -> TraceLog:
    """Настроить журнал по разобранным аргументам и напечатать шапку запуска."""
    path = log_path_for(args, default_dir=default_dir, prefix=prefix, module=module)
    log_obj = configure(
        log_path=path,
        verbose=not getattr(args, "quiet", False),
        trace=bool(getattr(args, "trace", False)),
        to_stderr=to_stderr or bool(getattr(args, "json", False)),
    )
    if header:
        title = f"{header} — журнал {_dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        log_obj.section(title)
        log_obj.kv("журнал", log_obj.log_path or "<только консоль>", level="info")
        log_obj.kv("командная строка", log_obj.command_line([Path(sys.argv[0]).name, *sys.argv[1:]]), level="info")
    return log_obj


# --------------------------------------------------------------------------- #
# починка кодировки: cmd.exe дописывает свои строки в OEM-кодировке
# --------------------------------------------------------------------------- #

def _has_surrogates(text: str) -> bool:
    """Есть ли в тексте байты, не сложившиеся в UTF-8 (``surrogateescape``)."""
    return any(0xDC80 <= ord(character) <= 0xDCFF for character in text)


def repair_mixed_encoding(path: Path, *, codepages: Sequence[str] = ("cp866", "cp1251")) -> int:
    """Перекодировать файл журнала в чистый UTF-8, если его дописал ``cmd.exe``.

    ``build.bat`` пишет свои строки через ``echo >>``, то есть в кодировке
    консоли (обычно cp866). Пока строки ASCII, разницы нет; но если в путь
    попала кириллица, в файле появляются чужие байты. Функция построчно
    проверяет UTF-8 и перекодирует «чужие» строки в UTF-8, ничего не теряя
    (порядок строк сохраняется). Возвращает число исправленных строк.
    """
    path = Path(path)
    if not path.is_file():
        return 0
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    if not raw:
        return 0
    text = raw.decode("utf-8", "surrogateescape")
    if not _has_surrogates(text):
        return 0  # уже нормальный UTF-8, переписывать файл незачем
    lines = text.split("\n")
    fixed = 0
    for index, line in enumerate(lines):
        if not _has_surrogates(line):
            continue
        raw_line = line.encode("utf-8", "surrogateescape")
        for codepage in codepages:
            try:
                lines[index] = raw_line.decode(codepage)
                fixed += 1
                break
            except UnicodeDecodeError:
                continue
    if not fixed:
        return 0
    try:
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
    except OSError:
        return 0
    return fixed


def main(argv: Sequence[str] | None = None) -> int:
    """Мини-CLI: починить кодировку журнала, который дописал ``build.bat``."""
    parser = argparse.ArgumentParser(description="Служебные операции с журналом сборки.")
    parser.add_argument("--repair", metavar="ФАЙЛ", help="перекодировать журнал в UTF-8 "
                                                         "(cmd.exe пишет свои строки в OEM-кодировке)")
    parser.add_argument("--quiet", action="store_true", help="ничего не печатать без надобности")
    args = parser.parse_args(argv)
    if args.repair:
        fixed = repair_mixed_encoding(Path(args.repair))
        if not args.quiet and fixed:
            print(f"log repaired: {fixed} line(s) -> UTF-8")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover - ручной запуск
    raise SystemExit(main())
