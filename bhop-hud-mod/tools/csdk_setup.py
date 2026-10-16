#!/usr/bin/env python3
"""Автоустановка CSDK 12: скачивание, распаковка и проверка компилятора.

Раньше единственным ручным шагом сборки была установка CSDK: без
``resourcecompiler.exe`` движок Deadlock не грузит ``.xml``/``.css`` из аддона,
поэтому сборка честно останавливалась с кодом ``3``. Этот модуль закрывает шаг
целиком:

1. ищет уже установленный CSDK (``--csdk``, переменные окружения, типичные
   места — всё это умеет ``tools/paths.py``);
2. если его нет — скачивает архив Reduced CSDK 12 (Google Диск, с обходом
   страницы подтверждения и догрузкой недокачанного);
3. распаковывает его в безопасный каталог (**не** OneDrive / Desktop /
   Documents — там ломаются права и инструменты падают);
4. приводит раскладку к корню CSDK, где лежит ``resourcecompiler.exe``
   (``game/bin_cs2/win64``, ``bin/win64`` и другие известные варианты —
   см. ``paths.COMPILER_RELATIVE_PATHS``); в архиве может быть лишний
   корневой каталог;
5. проверяет результат и возвращает путь к компилятору.

Только стандартная библиотека: ``urllib`` вместо requests, ``zipfile`` /
``tarfile`` вместо внешних архиваторов. Для ``.7z``/``.rar`` нужен 7-Zip — его
модуль находит сам и (на Windows, если разрешено) ставит через winget.

Самостоятельное использование::

    python3 tools/csdk_setup.py                       # найти или скачать
    python3 tools/csdk_setup.py --install-dir C:\\csdk
    python3 tools/csdk_setup.py --url https://.../csdk.zip --url file:///mirror.zip
    python3 tools/csdk_setup.py --no-download         # только поиск

Коды возврата: ``0`` — компилятор готов, ``3`` — CSDK не найден и скачивание
запрещено, ``2`` — ошибка.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths as paths_mod  # noqa: E402

# --- откуда качаем ---------------------------------------------------------- #

#: Страница инструмента: там всегда актуальная ссылка на архив.
CSDK_PAGE_URL = "https://deadlockmodding.pages.dev/modding-tools/csdk-12"

#: Файл Google Диска, на который ведёт кнопка Download с той страницы.
CSDK_DRIVE_FILE_ID = "1-Z-4CszWQNudzwzs6e6abPsp5RGFOURS"

#: Прямая ссылка (новый endpoint; часто отдаёт файл без страницы подтверждения).
CSDK_DIRECT_URL = (
    "https://drive.usercontent.google.com/download"
    f"?id={CSDK_DRIVE_FILE_ID}&export=download&confirm=t"
)

#: Классическая ссылка: отдаёт HTML с токеном подтверждения для больших файлов.
CSDK_CONFIRM_URL = f"https://docs.google.com/uc?export=download&id={CSDK_DRIVE_FILE_ID}"

#: Источники по умолчанию, по порядку: новый endpoint, затем классический.
#: Оба ведут на один и тот же файл с той же страницы CSDK 12, но ведут себя
#: по-разному, когда Google Диск упирается в лимит скачиваний.
CSDK_DEFAULT_URLS: tuple[str, ...] = (CSDK_DIRECT_URL, CSDK_CONFIRM_URL)

#: Фразы, по которым видно, что Google Диск не отдаёт файл из-за лимита.
#:
#: Это не «страница подтверждения»: разбирать там нечего, файл просто
#: недоступен какое-то время (у больших публичных файлов — суточный лимит).
DRIVE_BLOCK_MARKERS: tuple[str, ...] = (
    "too many users have",
    "download quota exceeded",
    "quota exceeded",
    "you have exceeded your download quota",
    "sorry, you can't view or download this file at this time",
    "unusual traffic",
)

#: Внутреннее имя архива и каталога установки (так он называется в официальном
#: архиве: внутри лежит единственная папка ``Reduced_CSDK_12``).
ARCHIVE_NAME = "Reduced_CSDK_12"

USER_AGENT = "deadlock-bhop-trainer/csdk-setup (python-urllib)"

#: Reduced CSDK 12 весит гигабайты. Всё, что меньше — это HTML-страница или
#: сообщение об ошибке, а не архив.
MIN_ARCHIVE_BYTES = 8 * 1024 * 1024

#: Сколько байт страницы подтверждения читаем, чтобы вытащить токен.
MAX_HTML_PEEK = 1 << 20

#: Имя файла для недокачанного архива (в нём же накапливается догрузка).
PART_NAME = "download.part"

DEFAULT_TIMEOUT = 60
DEFAULT_ATTEMPTS = 3

#: Каталоги, в которые CSDK класть нельзя: синхронизация ломает права и
#: инструменты падают (об этом же предупреждает страница CSDK 12).
UNSAFE_PARTS = ("onedrive", "desktop", "documents", "dropbox", "icloud", "google drive", "yandex disk")

#: Как может называться исполняемый файл 7-Zip в PATH.
SEVEN_ZIP_CANDIDATES = ("7z", "7za", "7zr", "7zz")

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_MISSING = 3


class CsdkError(Exception):
    """Установка CSDK не удалась (причина — в тексте)."""


# --- результат -------------------------------------------------------------- #


@dataclass
class SetupResult:
    """Итог работы :func:`ensure_csdk`."""

    root: Path | None = None
    compiler: Path | None = None
    status: str = "missing"  # found | downloaded | missing | disabled | error
    archive: Path | None = None
    url: str | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.compiler is not None

    def say(self, message: str) -> None:
        self.messages.append(message)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "root": str(self.root) if self.root else None,
            "compiler": str(self.compiler) if self.compiler else None,
            "archive": str(self.archive) if self.archive else None,
            "url": self.url,
            "messages": self.messages,
        }


# --- куда ставить ----------------------------------------------------------- #


def unsafe_dir_reason(path: Path | str) -> str | None:
    """Почему в этот каталог CSDK класть нельзя (``None`` — можно)."""
    text = str(path).lower()
    for part in UNSAFE_PARTS:
        if part in text:
            return f"путь содержит «{part}»: синхронизация ломает права, инструменты CSDK падают"
    return None


def default_install_root() -> Path:
    """Каталог установки по умолчанию: пользовательский и вне синхронизации.

    Windows: ``%LOCALAPPDATA%\\DeadlockModding\\Reduced_CSDK_12`` (если сам
    LOCALAPPDATA вдруг внутри OneDrive — ``<SystemDrive>\\DeadlockModding``).
    Остальное: ``~/.local/share/deadlock-csdk/Reduced_CSDK_12``.
    """
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "DeadlockModding" / ARCHIVE_NAME)
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        candidates.append(Path(system_drive + "\\") / "DeadlockModding" / ARCHIVE_NAME)
    home = Path.home()
    candidates.append(home / ".local" / "share" / "deadlock-csdk" / ARCHIVE_NAME)
    candidates.append(Path.cwd() / "csdk")

    for candidate in candidates:
        if unsafe_dir_reason(candidate) is None:
            return candidate
    # Если и это не помогло — хотя бы не синхронизируемый каталог пользователя.
    return home / ".cache" / "deadlock-csdk" / ARCHIVE_NAME


def default_cache_dir(install_root: Path) -> Path:
    """Куда класть скачанный архив (рядом с каталогом установки)."""
    return install_root.parent / "csdk-cache"


# --- скачивание -------------------------------------------------------------- #


def _new_opener() -> urllib.request.OpenerDirector:
    """``urllib`` с cookie-jar: кука подтверждения Google Диска обязательна."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def _with_params(url: str, **params: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({key: value for key, value in params.items() if value})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _looks_like_html(chunk: bytes) -> bool:
    head = chunk.lstrip()[:256].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<title>" in head[:256]


def _drive_block_reason(html: str) -> str | None:
    """Почему Google Диск не отдаёт файл (``None`` — это обычная страница).

    У больших публичных файлов Google Диска есть суточный лимит скачиваний, и
    вместо файла приходит страница «Too many users have viewed or downloaded this
    file». Разбирать её бессмысленно: подтверждения там нет, помочь может только
    другой источник, кэш или ожидание.
    """
    lowered = html.lower()
    for marker in DRIVE_BLOCK_MARKERS:
        if marker in lowered:
            return (
                "Google Диск не отдаёт архив: сработал лимит скачиваний "
                f"«{marker}». Это не ошибка сборки — файл временно недоступен."
            )
    return None


def _manual_hints(cache_dir: Path | None = None) -> str:
    """Что делать, когда автоскачивание CSDK не работает."""
    where = str(cache_dir) if cache_dir else r"%LOCALAPPDATA%\DeadlockModding\csdk-cache"
    return (
        f"  Страница с архивом: {CSDK_PAGE_URL}\n"
        "  Скачай Reduced CSDK 12 вручную и передай архив сборщику одним из способов:\n"
        "   • положить архив в кэш (следующий запуск возьмёт его сам):\n"
        f"     {where};\n"
        '   • build.bat --csdk-url "file:///C:/путь/Reduced_CSDK_12.zip";\n'
        '   • распаковать архив и указать каталог: build.bat --csdk "C:\\Reduced_CSDK_12".'
    )


def _confirm_tokens(html: str) -> dict[str, str]:
    """Вытащить ``confirm``/``uuid`` из страницы подтверждения Google Диска."""
    tokens: dict[str, str] = {}
    patterns = {
        "confirm": (
            r'name="confirm"\s+value="([^"]+)"',
            r"[?&]confirm=([0-9A-Za-z_-]{4,})",
            r'"confirm"\s*[:,]\s*"([0-9A-Za-z_-]{4,})"',
        ),
        "uuid": (
            r'name="uuid"\s+value="([^"]+)"',
            r"[?&]uuid=([0-9A-Za-z_-]{4,})",
            r'"uuid"\s*[:,]\s*"([0-9A-Za-z_-]{4,})"',
        ),
    }
    for key, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, html)
            if match:
                tokens[key] = match.group(1)
                break
    return tokens


def _suggested_name(response: object, url: str) -> str:
    """Имя файла из ``Content-Disposition`` или из самого URL."""
    disposition = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        disposition = str(headers.get("Content-Disposition", "") or "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    if match:
        name = urllib.parse.unquote(match.group(1).strip().strip('"'))
        if name:
            return _safe_name(name)
    path_name = _safe_name(urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]))
    return path_name


def _safe_name(name: str) -> str:
    """Выкинуть из имени всё, кроме «мирных» символов."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or f"{ARCHIVE_NAME}.bin"


#: Куда печатать прогресс. ``None`` — в stdout; сборщик в режиме ``--json``
#: переключает на stderr, чтобы stdout остался чистым JSON.
_PROGRESS_STREAM: object = None


def set_progress_stream(stream: object) -> None:
    """Задать поток для строки прогресса (``None`` — вернуть stdout)."""
    global _PROGRESS_STREAM
    _PROGRESS_STREAM = stream


def _progress_stream():
    return _PROGRESS_STREAM if _PROGRESS_STREAM is not None else sys.stdout


def _default_progress(done: int, total: int | None) -> None:
    stream = _progress_stream()
    if total:
        percent = min(100.0, done * 100.0 / total)
        print(f"\r    скачано {done / 1048576:.1f} / {total / 1048576:.1f} МБ ({percent:.0f}%)   ",
              end="", flush=True, file=stream)
    else:
        print(f"\r    скачано {done / 1048576:.1f} МБ   ", end="", flush=True, file=stream)


def _open_stream(url: str, offset: int, opener: urllib.request.OpenerDirector, timeout: int):
    """Открыть поток с ``offset``; вернуть ``(response, offset, первый_кусок)``."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if offset > 0:
        request.add_header("Range", f"bytes={offset}-")
    response = opener.open(request, timeout=timeout)
    status = int(getattr(response, "status", 200) or 200)
    if offset > 0 and status != 206:
        # Сервер не умеет догрузку — начинаем с нуля.
        response.close()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        response = opener.open(request, timeout=timeout)
        offset = 0
    elif offset > 0:
        # 206: сервер подтвердил, что отдаёт хвост.
        pass
    return response, offset, response.read(65536)


def download(
    url: str,
    directory: Path,
    *,
    filename: str | None = None,
    sha256: str | None = None,
    progress: Callable[[int, int | None], None] | None = None,
    log: Callable[[str], None] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    opener: urllib.request.OpenerDirector | None = None,
    redownload: bool = False,
) -> Path:
    """Скачать файл в ``directory`` и вернуть путь к готовому архиву.

    Поддерживается догрузка: недокачанный файл остаётся рядом как
    ``download.part``, следующий запуск продолжает с того же места (HTTP Range).
    Страница подтверждения Google Диска обходится автоматически.
    """
    log = log or (lambda message: None)
    progress = progress or _default_progress
    opener = opener or _new_opener()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    part = directory / PART_NAME
    url_marker = directory / (PART_NAME + ".url")
    if redownload:
        part.unlink(missing_ok=True)
        url_marker.unlink(missing_ok=True)
    if url_marker.is_file():
        try:
            if url_marker.read_text(encoding="utf-8").strip() != url:
                # Другой источник — старый недокачанный хвост не годится.
                part.unlink(missing_ok=True)
        except OSError:
            pass

    pending_url = url
    last_error: Exception | None = None
    attempt = 0
    confirm_rounds = 0
    while attempt < attempts:
        attempt += 1
        offset = part.stat().st_size if part.is_file() else 0
        if offset:
            log(f"· докачиваю: уже есть {offset / 1048576:.1f} МБ")
        try:
            response, offset, first = _open_stream(pending_url, offset, opener, timeout)
        except (urllib.error.URLError, OSError) as error:
            last_error = error
            log(f"! не удалось начать скачивание (попытка {attempt}/{attempts}): {error}")
            if attempt < attempts:
                time.sleep(min(5 * attempt, 15))
            continue

        with response:
            if _looks_like_html(first):
                body = (first + response.read(MAX_HTML_PEEK)).decode("utf-8", "replace")
                blocked = _drive_block_reason(body)
                if blocked is not None:
                    raise CsdkError(f"{blocked}\n{_manual_hints(directory)}")
                tokens = _confirm_tokens(body)
                if "confirm" not in tokens:
                    raise CsdkError(
                        "Google Диск не отдал файл, а страницу подтверждения разобрать не удалось.\n"
                        f"{_manual_hints(directory)}"
                    )
                confirm_rounds += 1
                if confirm_rounds > 2:
                    raise CsdkError(
                        "Google Диск упорно отдаёт страницу подтверждения вместо файла.\n"
                        f"{_manual_hints(directory)}"
                    )
                pending_url = _with_params(pending_url, confirm=tokens["confirm"], uuid=tokens.get("uuid", ""))
                log("· Google Диск просит подтверждение — повторяю запрос с токеном")
                # Рукопожатие подтверждения — не ошибка сети, попытку не тратим.
                attempt -= 1
                continue

            total_header = response.headers.get("Content-Length") if response.headers else None
            total = int(total_header) + offset if total_header and total_header.isdigit() else None
            name = filename or _suggested_name(response, pending_url) or f"{ARCHIVE_NAME}.bin"
            target = directory / _safe_name(name)
            try:
                url_marker.write_text(url, encoding="utf-8")
            except OSError:
                pass

            try:
                with open(part, "ab" if offset else "wb") as handle:
                    done = offset
                    if first:
                        handle.write(first)
                        done += len(first)
                    progress(done, total)
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        progress(done, total)
            except OSError as error:
                last_error = error
                log(f"! обрыв во время скачивания (попытка {attempt}/{attempts}): {error}")
                if attempt < attempts:
                    time.sleep(min(5 * attempt, 15))
                continue
            finally:
                print("", flush=True, file=_progress_stream())  # закрыть строку прогресса

        size = part.stat().st_size if part.is_file() else 0
        if size < MIN_ARCHIVE_BYTES:
            raise CsdkError(
                f"скачанный файл подозрительно мал ({size} Б) — похоже, это не архив CSDK.\n"
                f"  Проверь ссылку: {CSDK_PAGE_URL}\n"
                "  Свою ссылку/файл можно передать через --url."
            )
        if sha256:
            actual = file_sha256(part)
            if actual.lower() != sha256.lower():
                raise CsdkError(
                    f"контрольная сумма не совпадает: ожидалась {sha256.lower()}, получена {actual}.\n"
                    "  Файл мог докачаться с ошибкой — удали его или запусти с --redownload."
                )
            log(f"· sha256 совпал: {actual[:16]}…")
        if target.exists():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        part.replace(target)
        url_marker.unlink(missing_ok=True)
        log(f"· архив: {target} ({size / 1048576:.1f} МБ)")
        return target

    raise CsdkError(
        f"не удалось скачать CSDK за {attempts} попыток: {last_error}\n"
        f"  Ссылка: {pending_url}\n"
        f"  Ручной вариант: {CSDK_PAGE_URL} и затем --csdk PATH"
    )


def split_urls(value: str | Sequence[str] | None) -> list[str]:
    """Разобрать ссылки: одна строка (несколько — через ``;``/``,``/пробел) или список."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    urls: list[str] = []
    for item in items:
        for chunk in re.split(r"[;,\s]+", str(item).strip()):
            if chunk and chunk not in urls:
                urls.append(chunk)
    return urls


def download_many(
    urls: Sequence[str],
    directory: Path,
    *,
    filename: str | None = None,
    sha256: str | None = None,
    progress: Callable[[int, int | None], None] | None = None,
    log: Callable[[str], None] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    opener: urllib.request.OpenerDirector | None = None,
    redownload: bool = False,
) -> Path:
    """Скачать архив, перебирая источники по порядку.

    Официальная ссылка одна, но у неё есть два варианта endpoint'а, и любой из
    них может упереться в лимит Google Диска. Если источник не отдал файл —
    пробуем следующий, а в конце поднимаем ошибку с причинами по каждому.
    """
    log = log or (lambda message: None)
    sources = [url for url in urls if url]
    if not sources:
        raise CsdkError("не передано ни одной ссылки на архив CSDK")
    problems: list[str] = []
    for index, url in enumerate(sources, start=1):
        if len(sources) > 1:
            log(f"· источник {index}/{len(sources)}: {url}")
        try:
            return download(
                url, directory, filename=filename, sha256=sha256, progress=progress,
                log=log, timeout=timeout, attempts=attempts, opener=opener,
                redownload=redownload and index == 1,
            )
        except CsdkError as error:
            problems.append(f"{url}: {error}")
            if index < len(sources):
                log(f"! источник не сработал: {error}")
    raise CsdkError(
        "не удалось скачать архив CSDK ни с одного источника:\n  "
        + "\n  ".join(problems)
        + f"\n{_manual_hints(Path(directory))}"
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- распаковка -------------------------------------------------------------- #


def archive_kind(path: Path) -> str:
    """Тип архива по магическим байтам (расширению тоже верим, но не слепо)."""
    with open(path, "rb") as handle:
        magic = handle.read(8)
    if magic[:2] == b"PK":
        return "zip"
    if magic[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if magic[:4] == b"Rar!":
        return "rar"
    if magic[:2] == b"\x1f\x8b":
        return "gz"
    if magic[:3] == b"BZh":
        return "bz2"
    if magic[:6] == b"\xfd7zXZ\x00":
        return "xz"
    suffix = path.suffix.lower()
    if suffix in {".zip", ".7z", ".rar", ".gz", ".tgz", ".bz2", ".xz", ".txz", ".tar"}:
        return {".tgz": "gz", ".txz": "xz"}.get(suffix, suffix.lstrip("."))
    raise CsdkError(
        f"непонятный формат архива: {path}\n"
        "  Ожидался .zip / .7z / .rar / tar.* (именно так распространяется Reduced CSDK 12)."
    )


def find_seven_zip() -> Path | None:
    """Найти 7-Zip (PATH + обычные места установки)."""
    for name in SEVEN_ZIP_CANDIDATES:
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        for relative in ("7-Zip/7z.exe", "Programs/7-Zip/7z.exe"):
            candidate = Path(root) / relative
            if candidate.is_file():
                return candidate
    return None


def install_seven_zip(log: Callable[[str], None]) -> Path | None:
    """Поставить 7-Zip через winget (Windows). Возвращает путь к exe или None."""
    if not sys.platform.startswith("win") or shutil.which("winget") is None:
        return None
    log("· 7-Zip не найден — ставлю его через winget (нужен для .7z/.rar)")
    try:
        subprocess.run(
            ["winget", "install", "-e", "--id", "7zip.7zip",
             "--accept-source-agreements", "--accept-package-agreements"],
            check=False, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log(f"! winget не сработал: {error}")
        return None
    # winget не обновляет PATH в уже открытом окне — ищем по обычным путям.
    found = find_seven_zip()
    if found:
        log(f"· 7-Zip: {found}")
    return found


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Отсечь пути, уходящие за пределы каталога распаковки (zip-slip)."""
    safe: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise CsdkError(f"в архиве опасный путь: {info.filename}")
        safe.append(info)
    return safe


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zipped:
        members = _safe_members(zipped)
        zipped.extractall(destination, members=members)
        # zipfile не восстанавливает биты прав, а в архиве лежат исполняемые
        # файлы (resourcecompiler.exe, csdkcfg.exe). На Windows это неважно, но
        # распаковка под Linux/Proton иначе даёт неисполняемый компилятор.
        for info in members:
            mode = (info.external_attr >> 16) & 0o777
            if not mode & 0o111:
                continue
            target = destination / info.filename
            try:
                target.chmod(mode)
            except OSError:
                pass


def _extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(destination, filter="data")  # Python 3.12+
        except TypeError:
            tar.extractall(destination)


def _extract_with_7z(archive: Path, destination: Path, seven_zip: Path, log: Callable[[str], None]) -> None:
    result = subprocess.run(
        [str(seven_zip), "x", str(archive), f"-o{destination}", "-y", "-bso0", "-bsp0"],
        capture_output=True, text=True, timeout=7200,
    )
    if result.returncode != 0:
        raise CsdkError(
            f"7-Zip не смог распаковать {archive} (код {result.returncode}).\n"
            f"  {(result.stdout or '')[-800:]}{(result.stderr or '')[-800:]}\n"
            "  Распакуй архив вручную и укажи каталог через --csdk PATH."
        )
    log(f"· распаковано 7-Zip: {archive.name}")


def extract_archive(
    archive: Path,
    destination: Path,
    *,
    allow_tool_install: bool = True,
    log: Callable[[str], None] | None = None,
) -> None:
    """Распаковать архив CSDK в ``destination`` (каталог создаётся с нуля)."""
    log = log or (lambda message: None)
    kind = archive_kind(archive)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    log(f"· распаковка {archive.name} ({kind}) в {destination}")

    if kind == "zip":
        _extract_zip(archive, destination)
    elif kind in {"gz", "bz2", "xz", "tar"}:
        _extract_tar(archive, destination)
    elif kind in {"7z", "rar"}:
        seven_zip = find_seven_zip()
        if seven_zip is None and allow_tool_install:
            seven_zip = install_seven_zip(log)
        if seven_zip is None:
            raise CsdkError(
                f"архив {archive.name} в формате {kind}, а 7-Zip не найден.\n"
                "  Поставь 7-Zip (winget install -e --id 7zip.7zip) и запусти сборку заново,\n"
                "  либо распакуй архив вручную и укажи каталог через --csdk PATH."
            )
        _extract_with_7z(archive, destination, seven_zip, log)
    else:  # pragma: no cover - archive_kind уже отфильтровал
        raise CsdkError(f"неподдерживаемый формат архива: {kind}")


def _compiler_root(root: Path) -> Path | None:
    """Найти внутри ``root`` каталог, который действительно является CSDK.

    Архив может приехать с лишним корневым каталогом (``Reduced_CSDK_12/``), а
    раскладки бывают разные: бинарники лежат либо под ``game/bin*``, либо прямо
    в корне (``bin``, ``bin_cs2``, ``bin_server``, ``bin_tools``) — как в текущем
    Reduced CSDK 12. Поэтому ищем по обеим схемам.
    """
    candidates: list[Path] = [root]
    patterns = (
        "*/game/bin*/win64/resourcecompiler*",
        "*/*/game/bin*/win64/resourcecompiler*",
        "*/bin*/win64/resourcecompiler*",
        "*/*/bin*/win64/resourcecompiler*",
    )
    for pattern in patterns:
        for found in sorted(root.glob(pattern)):
            for ancestor in found.parents:
                if paths_mod.csdk_compiler(ancestor) is not None and ancestor not in candidates:
                    candidates.append(ancestor)
                if ancestor == root:
                    break
    valid = [candidate for candidate in candidates if paths_mod.csdk_compiler(candidate) is not None]
    if not valid:
        return None
    # Берём самый внешний каталог: если архив приехал с лишней папкой-обёрткой
    # (``Reduced_CSDK_12/``), корень CSDK — именно она, а не вложенный каталог.
    return min(valid, key=lambda path: (len(path.parts), str(path)))


def _move_contents(source: Path, destination: Path) -> None:
    """Перенести содержимое ``source`` в ``destination`` (сливая каталоги)."""
    destination.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        target = destination / entry.name
        if target.exists():
            if entry.is_dir() and target.is_dir():
                _move_contents(entry, target)
                continue
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(entry), str(target))


def install_from_archive(
    archive: Path,
    install_root: Path,
    *,
    allow_tool_install: bool = True,
    log: Callable[[str], None] | None = None,
) -> Path | None:
    """Распаковать ``archive`` в ``install_root`` и вернуть путь к компилятору.

    Распаковка идёт во временный каталог рядом с целевым: если что-то сорвётся,
    готовая установка не пострадает, а результат переезжает на место целиком.
    """
    log = log or (lambda message: None)
    install_root = Path(install_root)
    staging = install_root.with_name(install_root.name + ".unpacking")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        extract_archive(archive, staging, allow_tool_install=allow_tool_install, log=log)
        actual = _compiler_root(staging)
        if actual is None:
            raise CsdkError(
                f"в распакованном архиве нет resourcecompiler.exe (искали game/bin_cs2/win64,\n"
                f"  game/bin/win64, bin/win64 и другие раскладки): {archive}\n"
                "  Проверь, что это именно Reduced CSDK 12, и укажи каталог вручную: --csdk PATH"
            )
        if actual != staging:
            log(f"· привожу раскладку к корню CSDK (внутри был каталог {actual.name})")
            _move_contents(actual, staging)
            shutil.rmtree(actual, ignore_errors=True)
        compiler = paths_mod.csdk_compiler(staging)
        if compiler is None:
            raise CsdkError(f"после распаковки в {staging} компилятор не читается")
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(install_root))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return paths_mod.csdk_compiler(install_root)


# --- единая точка входа ------------------------------------------------------ #


def ensure_csdk(
    *,
    explicit: Path | str | None = None,
    install_root: Path | str | None = None,
    url: str | Sequence[str] | None = None,
    sha256: str | None = None,
    cache_dir: Path | str | None = None,
    allow_download: bool = True,
    redownload: bool = False,
    force_install: bool = False,
    allow_tool_install: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int | None], None] | None = None,
) -> SetupResult:
    """Найти CSDK, а если его нет — скачать и установить.

    Порядок: явный ``--csdk`` → переменные окружения и типичные места
    (``paths.find_csdk``) → скачивание в ``install_root``. ``force_install``
    отключает поиск существующих копий и предназначен для восстановления
    повреждённой установки из доверенного архива.
    """
    log = log or (lambda message: None)
    result = SetupResult()

    found = None if force_install else paths_mod.find_csdk(Path(explicit) if explicit else None)
    if found is not None:
        result.status = "found"
        result.root = Path(found)
        result.compiler = paths_mod.csdk_compiler(result.root)
        result.say(f"CSDK уже установлен: {result.root}")
        return result

    if not allow_download:
        result.status = "disabled"
        result.say(
            "CSDK не найден, автоскачивание отключено (--no-download).\n"
            f"  Скачай вручную: {CSDK_PAGE_URL} и передай путь через --csdk"
        )
        return result

    target_root = Path(install_root) if install_root else default_install_root()
    target_cache = Path(cache_dir) if cache_dir else default_cache_dir(target_root)
    sources = split_urls(url) or split_urls(os.environ.get("DEADLOCK_CSDK_URL")) or list(CSDK_DEFAULT_URLS)
    source_url = sources[0]
    cached = _cached_archive(target_cache)

    # Распаковывали ли сюда раньше. Каталог по умолчанию paths.find_csdk видит
    # и сам, а вот свой --csdk-install-dir надо проверить отдельно: иначе каждый
    # запуск заново разворачивал бы архив.
    compiler = None if force_install else paths_mod.csdk_compiler(target_root)
    if compiler is not None:
        result.status = "found"
        result.root = target_root
        result.compiler = compiler
        result.say(f"CSDK уже установлен: {target_root}")
        return result

    reason = unsafe_dir_reason(target_root)
    if reason is not None:
        raise CsdkError(
            f"нельзя ставить CSDK в {target_root}: {reason}.\n"
            "  Задай другой каталог: --csdk-install-dir PATH"
        )

    if sys.platform.startswith("win") and len(str(target_root)) > 60:
        # Внутри CSDK встречаются глубокие пути: от длины корня напрямую зависит,
        # упрёмся ли мы в ограничение Windows MAX_PATH при распаковке.
        log(f"! путь установки длинный ({len(str(target_root))} символов): при распаковке можно "
            "упереться в ограничение Windows на длину пути — лучше задать покороче, "
            "например --csdk-install-dir C:\\csdk12")

    log("=" * 60)
    log("  CSDK 12 не найден — скачиваю и устанавливаю сам")
    log("=" * 60)
    log(f"  куда:      {target_root}")
    log(f"  откуда:    {', '.join(sources)}")
    log(f"  кэш:       {target_cache}")

    try:
        if cached is not None and not redownload:
            log(f"· в кэше уже есть архив: {cached} — распаковываю, не качая заново")
            result.archive = cached
        if result.archive is None:
            result.url = source_url
            try:
                result.archive = download_many(
                    sources, target_cache, sha256=sha256, log=log, progress=progress,
                    timeout=timeout, redownload=redownload,
                )
            except CsdkError:
                # Скачать свежий архив не вышло (лимит Google Диска, сеть), но в
                # кэше лежит рабочий — лучше собрать мод на нём, чем остаться без
                # CSDK совсем. Молча этого не делаем: предупреждаем и говорим, как
                # обновить архив.
                if cached is None:
                    raise
                log(
                    "! свежий архив скачать не удалось — беру уже скачанный из кэша:\n"
                    f"  {cached}\n"
                    f"  Обновить его: удалить файл и запустить сборку с --csdk-redownload,\n"
                    f"  либо положить новый архив со страницы {CSDK_PAGE_URL} в {target_cache}"
                )
                result.say(
                    "свежий архив скачать не удалось (лимит Google Диска или сеть) — "
                    f"использую архив из кэша: {cached}"
                )
                result.archive = cached
        compiler = install_from_archive(
            result.archive, target_root, allow_tool_install=allow_tool_install, log=log
        )
    except (CsdkError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        result.status = "error"
        result.say(str(error))
        return result

    if compiler is None:
        result.status = "error"
        result.say(f"после установки в {target_root} компилятор не найден")
        return result

    result.status = "downloaded"
    result.root = Path(target_root)
    result.compiler = compiler
    result.say(f"CSDK установлен: {target_root}")
    result.say(f"компилятор: {compiler}")
    log(f"· готово: {compiler}")
    _describe_install(target_root, log=log, result=result)
    return result


def _describe_install(
    root: Path,
    *,
    log: Callable[[str], None],
    result: SetupResult | None = None,
) -> None:
    """Рассказать, что именно оказалось в распакованном CSDK.

    Полнота установки определяет, соберётся ли мод: компилятору нужен базовый мод
    (``game/citadel/gameinfo.gi``) и собственные модули инструментов
    (``modtools.dll`` и родня). Если чего-то нет — говорим об этом сразу, а не
    после получаса компиляции.
    """
    layout = paths_mod.csdk_layout(root)
    log(f"· раскладка CSDK: {layout.describe()}")
    if result is not None:
        result.say(f"раскладка CSDK: {layout.describe()}")
    if not layout.has_gameinfo:
        message = (
            "в CSDK нет game/citadel/gameinfo.gi — базового мода. Компилятор ищет его, "
            "чтобы понять, для какого мода собирать аддон: без файла сборка может упасть "
            "с «Can't find 'game\\citadel\\gameinfo.gi'».\n"
            "  Сборщик мода (build.bat) умеет закрыть это сам: он кладёт gameinfo.gi из "
            "установленной игры в дерево CSDK (--no-csdk-gameinfo-repair отключает).\n"
            "  Если помогает не всегда — в архиве старая раскладка CSDK: возьми текущий "
            f"Reduced CSDK 12 со страницы {CSDK_PAGE_URL}."
        )
        log(f"! {message}")
        if result is not None:
            result.say(message)
    if not layout.has_modules:
        message = (
            "рядом с компилятором нет файлов модулей инструментов (modtools.dll и т.п.): "
            "архив похож на неполный или очень старый. Если компилятор падает с "
            "«Unable to load module ... error 126», возьми текущий Reduced CSDK 12 "
            f"({CSDK_PAGE_URL}) или поставь Microsoft Visual C++ Redistributable 2015–2022 (x64)."
        )
        log(f"! {message}")
        if result is not None:
            result.say(message)


def _cached_archive(cache_dir: Path) -> Path | None:
    """Готовый архив в кэше (если он есть и не обрывок)."""
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return None
    candidates = [
        path for path in sorted(cache_dir.iterdir())
        if path.is_file() and path.name != PART_NAME
        and not path.name.endswith(".url") and path.stat().st_size >= MIN_ARCHIVE_BYTES
    ]
    if not candidates:
        return None
    # Свежий архив важнее: вдруг перекладывали руками.
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


# --- CLI -------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csdk_setup.py",
        description="Найти CSDK 12, а если его нет — скачать, распаковать и проверить.",
    )
    parser.add_argument("--csdk", help="уже установленный CSDK (иначе ищем сами)")
    parser.add_argument("--install-dir", help=f"куда распаковать (по умолчанию {default_install_root()})")
    parser.add_argument("--cache-dir", help="куда сложить скачанный архив")
    parser.add_argument(
        "--url",
        action="append",
        help="своя ссылка на архив CSDK: можно указать несколько (повторяя ключ или через ;) — "
             "источники пробуются по порядку (по умолчанию — Google Диск со страницы CSDK 12)",
    )
    parser.add_argument("--sha256", help="контрольная сумма архива (проверка после скачивания)")
    parser.add_argument("--no-download", action="store_true", help="только поиск, не скачивать")
    parser.add_argument("--redownload", action="store_true", help="игнорировать кэш и недокачанный файл")
    parser.add_argument("--force-install", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-tool-install", action="store_true", help="не ставить 7-Zip через winget")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="таймаут сети, секунды")
    parser.add_argument("--root-file", help="записать путь к найденному CSDK в этот файл (для build.bat)")
    parser.add_argument("--json", action="store_true", help="итог в JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda message: None) if args.json else (lambda message: print(message, flush=True))
    if args.json:
        set_progress_stream(sys.stderr)  # stdout оставляем под JSON

    try:
        result = ensure_csdk(
            explicit=args.csdk,
            install_root=args.install_dir,
            cache_dir=args.cache_dir,
            url=args.url,
            sha256=args.sha256,
            allow_download=not args.no_download,
            redownload=args.redownload,
            force_install=args.force_install,
            allow_tool_install=not args.no_tool_install,
            timeout=args.timeout,
            log=log,
        )
    except CsdkError as error:
        print(f"ОШИБКА: {error}", file=sys.stderr, flush=True)
        return EXIT_ERROR

    for message in result.messages:
        log(message)
    if result.root is not None:
        # Строчку понимает и build.bat, и CI-логи.
        if not args.json:
            print(f"CSDK_ROOT={result.root}", flush=True)
        if args.root_file:
            try:
                Path(args.root_file).write_text(str(result.root), encoding="utf-8")
            except OSError as error:
                print(f"! не удалось записать {args.root_file}: {error}", file=sys.stderr)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False), flush=True)

    if result.ok:
        return EXIT_OK
    return EXIT_MISSING if result.status == "disabled" else EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
