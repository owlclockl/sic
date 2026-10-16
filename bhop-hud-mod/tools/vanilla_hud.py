"""Откуда взять ванильный ``panorama/layout/hud.xml``.

Мод-панорама не может «добавить» панель в HUD, не переопределив layout, а
layout движок грузит из **скомпилированного** ресурса. Исходник для патча нужен
нам, но Valve кладёт в паки игры только скомпилированный
``panorama/layout/hud.vxml_c`` — сырых ``.xml``/``.css`` там нет вообще (это
видно по списку содержимого ``game/citadel/pak01_dir.vpk``). Поэтому «взять
hud.xml прямо из паков игры» само по себе не работает, и сборщик перебирает
источники по цепочке:

1. файл, указанный пользователем (``--hud-xml``);
2. «распакованный» файл в дереве игры или CSDK — ``panorama/layout/hud.xml``
   рядом с паками (так лежат выгрузки Source 2 Viewer и зеркала депо);
3. сырой ``panorama/layout/hud.xml`` внутри паков игры/CSDK (работает на
   старых сборках, где Valve ещё выкладывал исходники);
4. кэш прошлых сборок (``build/vanilla/hud.xml`` + метаданные);
5. скачивание подготовленного исходника с зеркала депо GameTracking
   (``SteamTracking/GameTracking-Deadlock``: raw.githubusercontent, CDN jsdelivr
   и GitHub API — какой из хостов доступен, тот и отвечает), отключается
   ``--no-hud-download``.

Кэш помнит, со сборки игры какого номера взят файл (``steam.inf`` →
``ClientVersion``), поэтому после патча игры сборщик предупреждает, если
ванильный layout устарел.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hud_patch  # noqa: E402
import trace_log  # noqa: E402
import vpk  # noqa: E402

#: Где лежит layout внутри пака/дерева.
HUD_XML_VIRTUAL_PATH = "panorama/layout/hud.xml"
#: То же, но в том виде, в каком файл реально есть в паках игры.
HUD_COMPILED_VIRTUAL_PATH = "panorama/layout/hud.vxml_c"

#: Зеркало депо (SteamDB/SteamTracking): там panorama-ресурсы выложены
#: распакованными (Source 2 Viewer → XML), потому что в паках лежит только
#: скомпилированный ресурс. Ветка ``master`` — основная у этого зеркала.
MIRROR_REPO = "SteamTracking/GameTracking-Deadlock"
MIRROR_REF = "master"
MIRROR_HUD_PATH = "game/citadel/pak01_dir/panorama/layout/hud.xml"
MIRROR_STEAM_INF_PATH = "game/citadel/steam.inf"

#: Зеркало депо (SteamTracking): там panorama-ресурсы выложены распакованными
#: (Source 2 Viewer → XML), потому что в паках лежит только скомпилированный
#: ресурс. Браузерный raw-хост местами недоступен, поэтому источников три:
#: raw.githubusercontent, CDN jsdelivr и GitHub API (отдаёт файл в JSON/base64).
MIRROR_URLS = (
    f"https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_REF}/{MIRROR_HUD_PATH}",
    f"https://cdn.jsdelivr.net/gh/{MIRROR_REPO}@{MIRROR_REF}/{MIRROR_HUD_PATH}",
    f"https://api.github.com/repos/{MIRROR_REPO}/contents/{MIRROR_HUD_PATH}?ref={MIRROR_REF}",
)

#: Тот же репозиторий, только ``steam.inf``: по нему видно, со сборки какого
#: номера взято зеркало (установленная игра пишет то же самое в свой steam.inf).
MIRROR_STEAM_INF_URLS = (
    f"https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_REF}/{MIRROR_STEAM_INF_PATH}",
    f"https://cdn.jsdelivr.net/gh/{MIRROR_REPO}@{MIRROR_REF}/{MIRROR_STEAM_INF_PATH}",
    f"https://api.github.com/repos/{MIRROR_REPO}/contents/{MIRROR_STEAM_INF_PATH}?ref={MIRROR_REF}",
)

USER_AGENT = "deadlock-bhop-trainer/vanilla-hud (python-urllib)"

#: Файл layout'а — десятки килобайт. Всё, что больше, это не он.
MAX_HUD_BYTES = 4 * 1024 * 1024

DEFAULT_TIMEOUT = 30
DEFAULT_ATTEMPTS = 2

CACHE_HUD_NAME = "hud.xml"
CACHE_META_NAME = "hud_source.json"

#: Куда ванильный файл может быть выгружен рядом с игрой/CSDK.
LOOSE_PARTS = (
    Path("panorama") / "layout",
    Path("pak01_dir") / "panorama" / "layout",
    Path("content") / "citadel" / "panorama" / "layout",
)

#: Папки внутри дерева игры, где ищем и «распакованное», и паки.
GAME_CONTENT_PARTS = (Path("game") / "citadel", Path("game") / "core")


class HudSourceError(Exception):
    """Ванильный hud.xml не найден ни в одном источнике (причина — в тексте)."""


@dataclass
class HudSource:
    """Найденный ванильный ``hud.xml`` и сведения о происхождении."""

    data: bytes
    label: str  # человекочитаемо: «паки игры C:\\Deadlock», «зеркало GameTracking» …
    origin: str  # машинно: explicit | loose | pak | cache | download
    url: str | None = None  # откуда скачано (источник)
    build_id: str | None = None  # сборка игры, к которой относится файл
    game_build_id: str | None = None  # сборка установленной игры (если игра есть)
    #: для кэша: сборка игры на момент скачивания файла
    fetched_for_game_build: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return vpk.sha256_of(self.data)

    @property
    def stale_for_game(self) -> bool:
        """``True`` — файл взят при другой сборке игры, чем стоит сейчас."""
        return bool(
            self.fetched_for_game_build
            and self.game_build_id
            and self.fetched_for_game_build != self.game_build_id
        )

    @property
    def build_id_matches_game(self) -> bool | None:
        """``None`` — сравнить не с чем."""
        if not self.build_id or not self.game_build_id:
            return None
        return self.build_id == self.game_build_id

    def to_json(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "label": self.label,
            "url": self.url,
            "sha256": self.sha256,
            "buildId": self.build_id,
            "gameBuildId": self.game_build_id,
            "fetchedForGameBuild": self.fetched_for_game_build,
        }


# --------------------------------------------------------------------------- #
# вспомогательное
# --------------------------------------------------------------------------- #


def game_build_id(game_dir: Path | None) -> str | None:
    """``ClientVersion`` из ``game/citadel/steam.inf`` — «номер сборки» игры."""
    if game_dir is None:
        return None
    for relative in (Path("game") / "citadel" / "steam.inf", Path("steam.inf")):
        path = Path(game_dir) / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower() == "clientversion" and value.strip():
                return value.strip()
    return None


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", "replace")


def check_hud_text(text: str) -> list[str]:
    """Проверить, что это действительно layout HUD. Пустой список — всё хорошо."""
    problems = list(hud_patch.validate_hud_xml(text))
    if not text.strip():
        problems.append("файл пуст")
    return problems


def _usable(data: bytes, problems: list[str], source: str) -> bool:
    """Проверить кандидата и записать причину в ``problems``, если он не годится."""
    text = _decode(data)
    found = check_hud_text(text)
    if found:
        problems.append(f"{source}: не похоже на ванильный hud.xml — {'; '.join(found)}")
        return False
    return True


def loose_candidates(game_dir: Path | None, csdk_root: Path | None) -> list[Path]:
    """Пути, где ванильный ``hud.xml`` может лежать файлом (без паков)."""
    roots: list[Path] = []
    if game_dir is not None:
        game_dir = Path(game_dir)
        roots += [game_dir / part for part in GAME_CONTENT_PARTS]
    if csdk_root is not None:
        csdk_root = Path(csdk_root)
        roots += [csdk_root / part for part in GAME_CONTENT_PARTS]
    found: list[Path] = []
    for root in roots:
        for part in LOOSE_PARTS:
            candidate = root / part / "hud.xml"
            if candidate not in found:
                found.append(candidate)
    return found


def pak_roots(game_dir: Path | None, csdk_root: Path | None) -> list[Path]:
    """Каталоги, в которых лежат ``pakNN_dir.vpk`` (игра и CSDK)."""
    roots: list[Path] = []
    for base in (game_dir, csdk_root):
        if base is None:
            continue
        for part in GAME_CONTENT_PARTS:
            root = Path(base) / part
            if root.is_dir() and root not in roots:
                roots.append(root)
    return roots


def could_override_layout(game_dir: Path | None) -> list[Path]:
    """Файлы в дереве игры, которые сам движок грузит раньше пака.

    Loose-файлы ``game/citadel/panorama/...`` перебивают содержимое пака, так
    что чужой (или полустёртый) ``hud.xml`` там — частая причина «панели нет».
    """
    if game_dir is None:
        return []
    citadel = Path(game_dir) / "game" / "citadel"
    found: list[Path] = []
    for part in (Path("panorama") / "layout" / "hud.xml",
                 Path("panorama") / "styles" / "hud.vcss"):
        path = citadel / part
        if path.is_file():
            found.append(path)
    return found


def fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Скачать файл (``http(s)`` и ``file``) целиком, с User-Agent."""
    if url.startswith("file:"):
        path = Path(urllib.request.url2pathname(urllib.parse.urlsplit(url).path))
        return path.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - ссылку даём сами
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_HUD_BYTES:
                raise HudSourceError(f"{url}: файл больше {MAX_HUD_BYTES} Б — это не layout HUD")
            chunks.append(chunk)
    return b"".join(chunks)


def decode_download(url: str, raw: bytes) -> bytes:
    """Ответ GitHub API — это JSON с base64; вернуть сам файл.

    Остальные источники (raw-хост, CDN, ``file://``, своя ссылка) отдают файл
    как есть, поэтому такие ответы не трогаем.
    """
    if "api.github.com/" not in url or "/contents/" not in url:
        return raw
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise HudSourceError(f"{url}: ответ GitHub API не разобрать как JSON — {error}") from error
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise HudSourceError(f"{url}: в ответе GitHub API нет base64-содержимого")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HudSourceError(f"{url}: в ответе GitHub API нет поля content")
    try:
        data = base64.b64decode(content, validate=False)
    except ValueError as error:
        raise HudSourceError(f"{url}: base64 из ответа GitHub API не разбирается — {error}") from error
    size = payload.get("size")
    if isinstance(size, int) and size != len(data):
        raise HudSourceError(f"{url}: размер файла не совпал: {size} против {len(data)} Б")
    return data


def _first_working(urls: Sequence[str], fetch: Callable[[str], bytes]) -> tuple[bytes, str]:
    """Скачать первый удачный URL; вернуть (данные, URL)."""
    errors: list[str] = []
    trace_log.detail(f"пробую ссылки на ванильный hud.xml: {len(urls)} шт.")
    for url in urls:
        try:
            raw = fetch(url)
            trace_log.detail(f"  {url}: получено {len(raw)} Б")
            data = decode_download(url, raw)
            trace_log.detail(f"  {url}: распаковано до {len(data)} Б, sha256 {trace_log.sha256_of(data)[:16]}…")
            return data, url
        except (urllib.error.URLError, OSError, HudSourceError, ValueError) as error:
            errors.append(f"{url}: {error}")
            trace_log.detail(f"  {url}: не вышло ({type(error).__name__}: {error})")
    raise HudSourceError("; ".join(errors))


# --------------------------------------------------------------------------- #
# кэш
# --------------------------------------------------------------------------- #


def _read_cache(cache_dir: Path | None) -> HudSource | None:
    """Прочитать ранее сохранённый (скачанный) ванильный файл."""
    if cache_dir is None:
        return None
    cache_dir = Path(cache_dir)
    hud = cache_dir / CACHE_HUD_NAME
    if not hud.is_file():
        return None
    try:
        data = hud.read_bytes()
    except OSError:
        return None
    problems: list[str] = []
    if not _usable(data, problems, str(hud)):
        trace_log.detail(f"кэш ванильного hud.xml не подошёл: {hud} ({'; '.join(problems)})")
        return None
    trace_log.detail(f"кэш ванильного hud.xml: {trace_log.describe(hud)}")
    meta: dict[str, object] = {}
    meta_path = cache_dir / CACHE_META_NAME
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError):
            meta = {}
    url = meta.get("url")
    build = meta.get("mirrorBuildId") or meta.get("buildId")
    fetched_for = meta.get("gameBuildId")
    return HudSource(
        data=data,
        label=f"кэш прошлых сборок: {hud}",
        origin="cache",
        url=str(url) if url else None,
        build_id=str(build) if build else None,
        fetched_for_game_build=str(fetched_for) if fetched_for else None,
    )


def _write_cache(cache_dir: Path | None, data: bytes, url: str, mirror_build: str | None,
                 game_build: str | None) -> Path | None:
    if cache_dir is None:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / CACHE_HUD_NAME
    target.write_bytes(data)
    meta = {
        "url": url,
        "sha256": vpk.sha256_of(data),
        "mirrorBuildId": mirror_build,
        "gameBuildId": game_build,
        "fetchedAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (cache_dir / CACHE_META_NAME).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    trace_log.path_event("ванильный hud.xml положен в кэш", target, size=len(data), sha256=meta["sha256"])
    trace_log.trace(f"  метаданные кэша: {meta}")
    return target


# --------------------------------------------------------------------------- #
# поиск по пакам
# --------------------------------------------------------------------------- #


def _scan_paks(roots: Sequence[Path], virtual_path: str) -> tuple[bytes | None, list[str], bool]:
    """Найти запись в паках. Вернуть (данные|None, проблемы, есть_ли_хоть_один_пак)."""
    problems: list[str] = []
    saw_pak = False
    for root in roots:
        candidates = vpk.find_pak_candidates(Path(root))
        trace_log.detail(f"паки в {root}: {[pak.name for pak in candidates] or 'нет'}")
        for pak in candidates:
            saw_pak = True
            try:
                archive = vpk.open_vpk(pak)
            except (vpk.VpkError, OSError) as error:
                problems.append(f"{pak.name}: не удалось прочитать ({error})")
                trace_log.detail(f"  {pak.name}: не прочитался ({error})")
                continue
            try:
                if not archive.contains(virtual_path):
                    trace_log.trace(f"  {pak.name}: {virtual_path} нет")
                    continue
                trace_log.detail(f"  {pak.name}: {virtual_path} найден")
                entry = archive.find(virtual_path)
                assert entry is not None
                try:
                    data = archive.read_entry(entry, verify_crc=True)
                except vpk.VpkError as error:
                    problems.append(f"{pak.name}: {virtual_path} — {error}")
                    continue
                return data, problems, True
            finally:
                archive.close()
    return None, problems, saw_pak


def _paks_without_sources(roots: Sequence[Path]) -> list[str]:
    """Что вместо исходника лежит в паках — для внятного текста ошибки."""
    found: list[str] = []
    for root in roots:
        for pak in vpk.find_pak_candidates(Path(root)):
            try:
                archive = vpk.open_vpk(pak)
            except (vpk.VpkError, OSError):
                continue
            try:
                if archive.contains(HUD_COMPILED_VIRTUAL_PATH):
                    found.append(f"{pak} → {HUD_COMPILED_VIRTUAL_PATH}")
            finally:
                archive.close()
    return found


# --------------------------------------------------------------------------- #
# основной вход
# --------------------------------------------------------------------------- #


def _no_source_message(
    problems: Sequence[str],
    game_dir: Path | None,
    csdk_root: Path | None,
    cache_dir: Path | None,
    game_build: str | None = None,
) -> str:
    lines = ["не нашёл ванильный panorama/layout/hud.xml — перепробовал всё:"]
    lines += [f"  • {problem}" for problem in problems] or ["  • источников не было вовсе"]
    lines += [
        "",
        "Почему так: Valve не кладёт исходные .xml/.css в паки игры — там только",
        "скомпилированный panorama/layout/hud.vxml_c. Исходник для патча надо взять отдельно.",
        "Варианты (любой):",
        "  1. Дать сети доступ и повторить сборку: сборщик сам скачает подготовленный",
        "     исходник с зеркала депо (SteamTracking/GameTracking-Deadlock).",
        "     Своя ссылка: --hud-xml-url https://…/hud.xml",
        "  2. Указать файл вручную: --hud-xml путь\\к\\hud.xml",
        "     Как достать: Source 2 Viewer →",
        "       <игра>\\game\\citadel\\pak01_dir.vpk → panorama\\layout\\hud.vxml_c",
        "       → «Decompile»/«Export» и сохранить как hud.xml.",
        "  3. Если ванильный hud.xml уже есть от прошлой сборки — просто передай его.",
    ]
    if game_dir is not None:
        lines.append(f"  Игра: {game_dir} (сборка {game_build or 'неизвестна'})")
        overrides = could_override_layout(game_dir)
        if overrides:
            lines.append(
                "  Внимание: в дереве игры лежат loose-файлы, перебивающие пак — "
                + ", ".join(str(path) for path in overrides)
            )
    if csdk_root is not None:
        lines.append(f"  CSDK: {csdk_root}")
    if cache_dir is not None:
        lines.append(f"  Кэш прошлых сборок: {Path(cache_dir) / CACHE_HUD_NAME}")
    return "\n".join(lines)


def resolve(
    *,
    game_dir: Path | None = None,
    csdk_root: Path | None = None,
    explicit: Path | None = None,
    cache_dir: Path | None = None,
    allow_download: bool = True,
    refresh: bool = False,
    urls: Sequence[str] | None = None,
    steam_inf_urls: Sequence[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    fetch: Callable[[str], bytes] | None = None,
    log: Callable[[str], None] | None = None,
    warn: Callable[[str], None] | None = None,
) -> HudSource:
    """Найти ванильный ``hud.xml``. Бросает :class:`HudSourceError`, если не вышло."""
    log = log or (lambda message: None)
    warn = warn or (lambda message: None)
    fetch = fetch or (lambda url: fetch_bytes(url, timeout))
    urls = tuple(urls) if urls else MIRROR_URLS
    steam_inf_urls = tuple(steam_inf_urls) if steam_inf_urls else MIRROR_STEAM_INF_URLS
    game_dir = Path(game_dir) if game_dir is not None else None
    csdk_root = Path(csdk_root) if csdk_root is not None else None
    problems: list[str] = []

    # 1. явный файл
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise HudSourceError(f"указанный hud.xml не найден: {path}")
        data = path.read_bytes()
        found = check_hud_text(_decode(data))
        if found:
            raise HudSourceError(
                f"{path}: это не похоже на ванильный panorama/layout/hud.xml — {'; '.join(found)}"
            )
        return HudSource(data=data, label=f"файл {path}", origin="explicit")

    game_build = game_build_id(game_dir)
    if game_build:
        log(f"сборка установленной игры: {game_build}")

    # 2. «распакованный» файл в дереве игры/CSDK
    for path in loose_candidates(game_dir, csdk_root):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if _usable(data, problems, str(path)):
            return HudSource(
                data=data,
                label=f"файл {path}",
                origin="loose",
                build_id=game_build,
                game_build_id=game_build,
            )

    # 3. сырой исходник внутри паков игры/CSDK
    roots = pak_roots(game_dir, csdk_root)
    if roots:
        data, pak_problems, saw_pak = _scan_paks(roots, HUD_XML_VIRTUAL_PATH)
        problems += pak_problems
        if data is not None:
            game_root = roots[0]
            return HudSource(
                data=data,
                label=f"паки игры {game_root.parent.parent}",
                origin="pak",
                build_id=game_build,
                game_build_id=game_build,
                notes=[f"в паках нашёлся исходный {HUD_XML_VIRTUAL_PATH}"],
            )
        if saw_pak:
            compiled = _paks_without_sources(roots)
            if compiled:
                problems.append(
                    "в паках игры нет исходного hud.xml — там только скомпилированный ресурс: "
                    + "; ".join(compiled)
                )
            else:
                problems.append(
                    f"в паках {', '.join(str(root) for root in roots)} нет ни "
                    f"{HUD_XML_VIRTUAL_PATH}, ни {HUD_COMPILED_VIRTUAL_PATH}"
                )
        else:
            problems.append(
                f"в {', '.join(str(root) for root in roots)} нет ни одного pak*_dir.vpk"
            )
    else:
        problems.append("дерево игры/CSDK не найдено — искать в паках негде")

    # 4. кэш прошлых сборок
    cached = _read_cache(cache_dir)
    if cached is not None:
        cached.game_build_id = game_build
        if refresh:
            log("кэш ванильного hud.xml есть, но запрошено обновление (--refresh-hud-xml)")
        elif cached.stale_for_game:
            # Игра обновилась после того, как файл попал в кэш: HUD мог
            # поменяться, поэтому сначала пробуем взять свежий.
            problems.append(
                f"кэш ванильного hud.xml взят при сборке игры {cached.fetched_for_game_build}, "
                f"а сейчас {game_build} — нужен свежий"
            )
        else:
            log(f"ванильный hud.xml из кэша: {cached.label}")
            return cached

    # 5. скачивание с зеркала
    if allow_download:
        try:
            raw, url = _first_working(urls, fetch)
        except HudSourceError as error:
            problems.append(f"скачать с зеркала не удалось: {error}")
        else:
            found = check_hud_text(_decode(raw))
            if found:
                problems.append(
                    f"скачанный с {url} файл не похож на ванильный hud.xml — {'; '.join(found)}"
                )
            else:
                mirror_build: str | None = None
                try:
                    inf, _ = _first_working(steam_inf_urls, fetch)
                except HudSourceError:
                    pass
                else:
                    mirror_build = _parse_client_version(_decode(inf))
                if mirror_build and game_build and mirror_build != game_build:
                    warn(
                        f"зеркало депо сейчас на сборке {mirror_build}, у тебя {game_build}: "
                        "ванильные файлы могут отличаться. После патча игры обнови мод."
                    )
                _write_cache(cache_dir, raw, url, mirror_build, game_build)
                log(f"ванильный hud.xml скачан: {url}")
                return HudSource(
                    data=raw,
                    label="зеркало депо GameTracking (SteamTracking/GameTracking-Deadlock)",
                    origin="download",
                    url=url,
                    build_id=mirror_build or game_build,
                    game_build_id=game_build,
                    fetched_for_game_build=game_build,
                    notes=[f"источник: {url}"],
                )
    else:
        problems.append("скачивание отключено (--no-hud-download), а локальных источников нет")

    if cached is not None:
        # Скачать не вышло, но кэш есть: собираем с ним и честно предупреждаем.
        warning = "скачать свежий ванильный hud.xml не удалось — собираю с кэшированным"
        if cached.stale_for_game:
            warning += (
                f" (он взят при сборке игры {cached.fetched_for_game_build}, "
                f"а сейчас {cached.game_build_id}) — после обновления игры HUD мог поменяться"
            )
        warn(warning)
        return cached

    raise HudSourceError(_no_source_message(problems, game_dir, csdk_root, cache_dir, game_build))


def _parse_client_version(text: str) -> str | None:
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "clientversion" and value.strip():
            return value.strip()
    return None


def iter_attempted_sources(
    game_dir: Path | None,
    csdk_root: Path | None,
    cache_dir: Path | None,
) -> Iterator[str]:
    """Короткое описание источников — для отчёта сборки."""
    if game_dir is not None:
        yield f"дерево игры: {game_dir}"
    if csdk_root is not None:
        yield f"дерево CSDK: {csdk_root}"
    for path in loose_candidates(game_dir, csdk_root):
        yield f"файл: {path}"
    for root in pak_roots(game_dir, csdk_root):
        yield f"паки: {root}"
    if cache_dir is not None:
        yield f"кэш: {Path(cache_dir) / CACHE_HUD_NAME}"
    for url in MIRROR_URLS:
        yield f"зеркало: {url}"
