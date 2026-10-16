#!/usr/bin/env python3
"""Автосборщик мода Deadlock :: Bhop Trainer.

Что делает по шагам (``--steps``):

0. внутри шага ``compile`` (если нет ``--compiler-cmd`` и
   ``--allow-uncompiled``) CSDK ищется, а при отсутствии скачивается и
   распаковывается сам (``tools/csdk_setup.py``). Отключить можно флагом
   ``--no-auto-csdk``. С ``--clean`` сборка идёт с нуля: рабочие каталоги
   удаляются перед стартом.

1. ``prepare`` — берёт ванильный ``panorama/layout/hud.xml`` (явный файл,
   распакованный файл в дереве игры/CSDK, сырой исходник внутри паков, кэш
   прошлых сборок или зеркало депо — см. ``tools/vanilla_hud.py``), вставляет
   туда панель из ``src/bhop_trainer.vxml`` и подключает
   ``src/bhop_trainer.vcss``; собирает дерево аддона
   ``content/citadel_addons/<addon>``. Отключить скачивание:
   ``--no-hud-download``; взять заново: ``--refresh-hud-xml``.
2. ``compile`` — прогоняет исходники через ``resourcecompiler.exe`` из CSDK
   (движок Deadlock не грузит сырые ``.xml``/``.css`` из аддона: нужны
   ``.vxml_c`` / ``.vcss_c``). Вызов — ``-nop4 -f -i <исходник>``, без
   ``-game``: аддон определяется по пути файла внутри
   ``content/citadel_addons/<addon>``, а ``-game`` ломает сборку на Reduced
   CSDK 12, где нет ``game/citadel/gameinfo.gi``. Если компилятор конкретной
   сборки требует ``-game``, сборщик повторит вызов с ``gameinfo.gi``
   установленной игры, а задать каталог вручную можно ключом
   ``--compiler-game``. Без компилятора шаг можно пропустить только явным
   ``--allow-uncompiled`` — тогда артефакты помечаются как «только исходники»
   и пакет для DMM не собирается.
3. ``pack`` — пакует скомпилированное дерево в VPK v2 (writer совместим с
   Deadlock Mod Manager) и вшивает метаданные ``vpk-modinfo v1``
   (``addoninfo.txt`` + ``modinfo.json``), затем проверяет результат чтением.
4. ``dmm`` — собирает пакет для Deadlock Mod Manager
   («Deadlock Mod Interchange») и zip для перетаскивания в «Add Mods».
5. ``report`` — ``dist/BUILD_REPORT.md`` и ``dist/SHA256SUMS.txt``.

Примеры:

    python3 tools/build_mod.py --game-path "/mnt/games/Deadlock" --csdk "/home/me/csdk"
    python3 tools/build_mod.py --hud-xml /tmp/hud.xml --compiler-cmd "python3 fake.py ..."
    python3 tools/build_mod.py --steps prepare --allow-uncompiled --json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import csdk_setup  # noqa: E402
import dmm_bundle  # noqa: E402
import hud_patch  # noqa: E402
import modinfo  # noqa: E402
import paths as paths_mod  # noqa: E402
import vanilla_hud  # noqa: E402
import vpk  # noqa: E402

TOOL = dmm_bundle.MANAGER_ID
#: 1.1.0: компиляция вызывается без `-game` (см. `_compiler_command`),
#: у отчёта появился честный статус и разбор ошибок `resourcecompiler`.
TOOL_VERSION = "1.1.0"

MOD_TITLE = "Deadlock :: Bhop Trainer HUD"
MOD_AUTHOR = "owlclockl"
MOD_CATEGORY = "UI/HUD"
MOD_DESCRIPTION = (
    "Панель-шпаргалка по банихопу поверх HUD Deadlock: шаги air strafe, слайд-хоп, "
    "даш-джамп, подсказки по клавишам. Клиентская косметика, без автоматизации ввода."
)
STYLE_SRC = "s2r://panorama/styles/bhop_trainer.vcss"
STYLES_FILENAME = "bhop_trainer.vcss"

#: Соответствие «исходник → скомпилированный ресурс».
#:
#: Panorama-исходники Valve пишет как ``.vcss``/``.vxml``/``.vjs``, а сборки
#: сообщества (QOLLOCK, QoL HUD) держат те же файлы как ``.css``/``.xml``/``.js``.
#: Оба варианта принимаются компилятором, и оба дают один и тот же ресурс —
#: ``bhop_trainer.vcss_c``; берём оба, чтобы не спорить о вкусах.
COMPILED_EXTENSIONS = {
    ".css": ".vcss_c",
    ".vcss": ".vcss_c",
    ".xml": ".vxml_c",
    ".vxml": ".vxml_c",
    ".js": ".vjs_c",
    ".vjs": ".vjs_c",
    ".svg": ".vsvg_c",
    ".png": ".vtex_c",
    ".tga": ".vtex_c",
    ".wav": ".vsnd_c",
    ".vsndevts": ".vsndevts_c",
    ".vdata": ".vdata_c",
}


EXIT_OK = 0
EXIT_ERROR = 2
EXIT_NEEDS_COMPILE = 3

def _rel(path: Path, base: Path) -> str:
    """Путь относительно базы, а если это невозможно — абсолютный.

    ``--out`` и ``--build-dir`` могут указывать куда угодно (в том числе за
    пределы репозитория), поэтому показывать пути надо устойчиво.
    """
    try:
        return Path(path).relative_to(base).as_posix()
    except ValueError:
        return str(path)


#: При ``--json`` человекочитаемые сообщения уходят в stderr, чтобы stdout
#: содержал только JSON (важно для CI).
_LOG_TO_STDERR = False


def log(message: str) -> None:
    # sys.stdout/sys.stderr берём в момент вызова, а не при импорте: так работает
    # и перенаправление вывода в тестах, и запуск из другого скрипта.
    print(message, file=sys.stderr if _LOG_TO_STDERR else sys.stdout, flush=True)


@dataclass
class Context:
    """Всё, что нужно шагам сборки."""

    root: Path  # .../bhop-hud-mod
    out_dir: Path
    build_dir: Path
    addon_name: str
    version: str
    args: argparse.Namespace
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)

    # -- пути --------------------------------------------------------------- #

    @property
    def src_dir(self) -> Path:
        return self.root / "src"

    @property
    def snippet_path(self) -> Path:
        return self.src_dir / "bhop_trainer.vxml"

    @property
    def styles_path(self) -> Path:
        return self.src_dir / STYLES_FILENAME

    @property
    def content_root(self) -> Path:
        """Дерево аддона в формате CSDK ``content/citadel_addons/<addon>``."""
        return self.build_dir / "content" / "citadel_addons" / self.addon_name

    @property
    def output_root(self) -> Path:
        """Куда компилятор кладёт ``*_c`` (``game/citadel_addons/<addon>``)."""
        return self.build_dir / "game" / "citadel_addons" / self.addon_name

    @property
    def vpk_path(self) -> Path:
        return self.out_dir / f"{self.addon_name}_dir.vpk"

    @property
    def bundle_dir(self) -> Path:
        return self.out_dir / "dmm-bundle"

    @property
    def zip_path(self) -> Path:
        return self.out_dir / f"{self.addon_name}-dmm.zip"

    @property
    def sources_only(self) -> bool:
        return bool(self.state.get("uncompiled"))

    def note(self, message: str) -> None:
        self.notes.append(message)
        log(f"  · {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        log(f"  ! {message}")


# --------------------------------------------------------------------------- #
# шаги
# --------------------------------------------------------------------------- #


def step_prepare(ctx: Context) -> None:
    """Собрать дерево аддона с пропатченным ``hud.xml``."""
    log("[1/4] prepare — дерево аддона и патч hud.xml")

    # Стадию компиляции начинаем с чистого листа: иначе в неё мог бы попасть
    # (и уехать в VPK) ресурс от прошлой сборки.
    if ctx.output_root.exists():
        shutil.rmtree(ctx.output_root)

    snippet = hud_patch.load_snippet(ctx.snippet_path)
    styles = ctx.styles_path.read_text(encoding="utf-8")
    ctx.state["snippet_sha256"] = vpk.sha256_of(snippet.encode("utf-8"))
    ctx.state["styles_sha256"] = vpk.sha256_of(styles.encode("utf-8"))

    hud_xml_arg = Path(ctx.args.hud_xml) if ctx.args.hud_xml else None
    game_dir = ctx.args.game_dir
    if hud_xml_arg is None and game_dir is None:
        game_dir = paths_mod.find_game_dir(None)
    csdk_root = (Path(ctx.args.csdk).expanduser() if ctx.args.csdk
                 else paths_mod.find_csdk(None))

    allow_download = bool(ctx.args.hud_download or ctx.args.hud_xml_url)
    if ctx.args.hud_xml_url and not ctx.args.hud_download:
        # Явная ссылка — это и есть просьба взять файл оттуда (бывает и file://).
        ctx.note("--hud-xml-url задан: файл берётся по этой ссылке, --no-hud-download не мешает")
    try:
        source = vanilla_hud.resolve(
            game_dir=game_dir,
            csdk_root=csdk_root,
            explicit=hud_xml_arg,
            cache_dir=ctx.build_dir / "vanilla",
            allow_download=allow_download,
            refresh=ctx.args.refresh_hud_xml,
            urls=[ctx.args.hud_xml_url] if ctx.args.hud_xml_url else None,
            log=ctx.note,
            warn=ctx.warn,
        )
    except vanilla_hud.HudSourceError as error:
        raise SystemExit(str(error))

    if source.origin == "loose":
        ctx.warn(
            "ванильный hud.xml взят файлом из дерева игры — если это не выгрузка Valve, "
            "а патч другого мода, лучше убрать его (или передать свой через --hud-xml)"
        )

    vanilla_bytes = source.data
    ctx.state["hud_source_label"] = source.label
    ctx.state["hud_source_origin"] = source.origin
    ctx.state["hud_source"] = (
        f"{game_dir}::{hud_patch.HUD_XML_VIRTUAL_PATH}" if source.origin == "pak"
        else (source.url or source.label)
    )
    if source.url:
        ctx.state["hud_source_url"] = source.url
    if source.build_id:
        ctx.state["hud_build_id"] = source.build_id
    if source.game_build_id:
        ctx.state["hud_game_build_id"] = source.game_build_id
    for message in source.notes:
        ctx.note(message)
    ctx.note(f"ванильный hud.xml: {source.label} (источник: {source.origin})")
    ctx.state["hud_vanilla_sha256"] = vpk.sha256_of(vanilla_bytes)
    vanilla_text = vanilla_bytes.decode("utf-8", "replace")
    ctx.note(f"sha256 ванильного hud.xml: {ctx.state['hud_vanilla_sha256'][:16]}…")

    patched = hud_patch.patch_hud_xml(vanilla_text, snippet=snippet, style_src=STYLE_SRC)
    if patched.already_patched:
        for message in patched.notes:
            ctx.note(message)
    ctx.state["hud_patched_sha256"] = vpk.sha256_of(patched.text.encode("utf-8"))

    layout_dir = ctx.content_root / "panorama" / "layout"
    styles_dir = ctx.content_root / "panorama" / "styles"
    layout_dir.mkdir(parents=True, exist_ok=True)
    styles_dir.mkdir(parents=True, exist_ok=True)
    (layout_dir / "hud.xml").write_text(patched.text, encoding="utf-8", newline="")
    (styles_dir / STYLES_FILENAME).write_text(styles, encoding="utf-8", newline="")

    vanilla_copy = ctx.build_dir / "vanilla" / "hud.xml"
    vanilla_copy.parent.mkdir(parents=True, exist_ok=True)
    vanilla_copy.write_bytes(vanilla_bytes)

    ctx.note(f"дерево аддона: {_rel(ctx.content_root, ctx.root)}")
    _write_state(ctx)


def _compiler_command(
    ctx: Context,
    source: Path,
    resolver: "_CompilerResolver",
    *,
    game: Path | None = None,
) -> list[str]:
    """Команда компиляции одного исходника.

    Без ``--compiler-cmd`` повторяем вызов из рабочих пайплайнов сообщества
    (QOLLOCK, Deadlock QoL HUD Mod, Deadlock Stats Editor)::

        resourcecompiler.exe -nop4 -f -i <путь к исходнику>

    ``-game`` по умолчанию **не** передаём: компилятор сам понимает, какой аддон
    собирает, по пути исходника внутри ``content/citadel_addons/<addon>``
    (``-game`` нужен только для файлов вне ``content/``). В Reduced CSDK 12 нет
    файлов самой игры — ``game/citadel/gameinfo.gi`` отсутствует, — и вызов с
    ``-game <CSDK>/game/citadel`` падает ещё до компиляции:

        Application unable to load gameinfo.gi file from directory "game\\citadel"
        Can't find 'game\\citadel\\gameinfo.gi'

    Если конкретной сборке CSDK ``-game`` всё-таки нужен, каталог с
    ``gameinfo.gi`` задаётся явно (``--compiler-game``) — он и попадает в
    команду. Дополнительно можно заменить всю команду через ``--compiler-cmd``.
    """
    template = ctx.args.compiler_cmd
    content_root = resolver.content_root
    output_root = resolver.output_root
    if not template:
        command = [str(resolver.compiler), "-nop4", "-f", "-i", str(source)]
        if game is not None:
            # Порядок как в рабочих пайплайнах: -game до -i.
            command[1:1] = ["-game", str(game)]
        return command
    mapping = {
        "compiler": str(resolver.compiler or ""),
        "sdk": str(resolver.sdk or ""),
        "game": str(game or resolver.game_dir or ""),
        "addon": ctx.addon_name,
        "source": str(source),
        "content_root": str(content_root),
        "output_root": str(output_root),
    }
    parts = shlex.split(template)
    return [part.format(**mapping) for part in parts]


@dataclass
class _CompilerResolver:
    """Где компилятор, где CSDK-деревья и откуда забирать результат."""

    compiler: Path | None
    sdk: Path | None
    game_dir: Path | None
    content_root: Path
    output_root: Path


@dataclass
class _CompiledRoot:
    """Каталог, в котором может оказаться скомпилированный ресурс.

    ``fresh_only`` — брать файл, только если он появился или изменился во время
    этого запуска (нужно для дерева самой игры: там может лежать чужой ресурс с
    тем же именем). ``cleanup`` — удалить файл после копирования, если он был
    создан этим запуском: сборка не должна оставлять мусор в файлах игры.
    """

    root: Path
    label: str
    fresh_only: bool = False
    cleanup: bool = False


def _resolve_compiler(ctx: Context) -> _CompilerResolver:
    args = ctx.args
    if args.compiler_cmd:
        # Своя команда: куда она кладёт результат, решает она сама. Дерево игры
        # передаём, только если оно известно (нужно для подсказок и для поиска
        # результата в дереве игры).
        return _CompilerResolver(
            compiler=None,
            sdk=Path(args.csdk).expanduser().resolve() if args.csdk else None,
            game_dir=Path(args.game_dir) if getattr(args, "game_dir", None) else None,
            content_root=ctx.content_root,
            output_root=ctx.output_root,
        )
    csdk = paths_mod.find_csdk(Path(args.csdk) if args.csdk else None)
    if csdk is None:
        return _CompilerResolver(
            compiler=None, sdk=None, game_dir=None,
            content_root=ctx.content_root, output_root=ctx.output_root,
        )
    compiler = paths_mod.csdk_compiler(csdk)
    return _CompilerResolver(
        compiler=compiler,
        sdk=csdk,
        game_dir=csdk / "game" / "citadel",
        content_root=csdk / "content" / "citadel_addons" / ctx.addon_name,
        output_root=csdk / "game" / "citadel_addons" / ctx.addon_name,
    )


def _compiled_roots(
    ctx: Context,
    resolver: "_CompilerResolver",
    *,
    game_dir: Path | None = None,
) -> list[_CompiledRoot]:
    """Куда компилятор может положить ``*_c``, в порядке проверки.

    Порядок отражает реальные раскладки:

    1. наш ``build/game/...`` — если компилятор вызван обёрткой с явным output-root;
    2. ``--compiled-from`` — путь, указанный пользователем явно;
    3. ``<CSDK>/game/citadel_addons/<addon>`` — штатное место CSDK;
    4. дерево самой игры (``<Deadlock>/game``) — так получается, если компилировать
       в дереве игры напрямую (или если ``-game`` указал на установленную игру);
       берём только свежие файлы и убираем их за собой.
    """
    roots = [_CompiledRoot(ctx.output_root, "каталог сборки")]

    def _add(root: Path, label: str, **flags: bool) -> None:
        if all(existing.root != root for existing in roots):
            roots.append(_CompiledRoot(root, label, **flags))

    if ctx.args.compiled_from:
        _add(Path(ctx.args.compiled_from).expanduser().resolve(), "--compiled-from")
    if resolver.output_root != ctx.output_root:
        _add(resolver.output_root, "дерево CSDK")
    if resolver.sdk is not None:
        # Даже со своей командой компиляции CSDK обычно пишет сюда.
        _add(resolver.sdk / "game" / "citadel_addons" / ctx.addon_name, "дерево CSDK")
    if resolver.game_dir is not None:
        _add(resolver.game_dir.parent, "дерево игры", fresh_only=True, cleanup=True)
    if game_dir is not None and Path(game_dir).parent != resolver.game_dir:
        # Установленная игра: сюда компилятор пишет, когда `-game` указывает на неё.
        _add(Path(game_dir).parent, "дерево установленной игры", fresh_only=True, cleanup=True)
    return roots


#: По этим словам в выводе ``resourcecompiler`` понятно, что он не смог решить,
#: для какого мода собирать (обычно — нет ``game\citadel\gameinfo.gi``).
_GAMEINFO_MARKERS = ("gameinfo.gi", "unable to load gameinfo", "can't find 'game")


def _gameinfo_trouble(result: "subprocess.CompletedProcess[str]") -> bool:
    """Похоже ли, что компилятор упал именно на поиске ``gameinfo.gi``."""
    text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return any(marker in text for marker in _GAMEINFO_MARKERS)


@dataclass(frozen=True)
class _CompileAttempt:
    """Вариант вызова компилятора: подпись для логов и аргумент ``-game``."""

    label: str
    game: Path | None = None


def _fallback_game_dir(ctx: Context) -> Path | None:
    """``<Deadlock>/game/citadel`` установленной игры — каталог с настоящим ``gameinfo.gi``.

    Нужен как запасной вариант для CSDK, который отказывается компилировать без
    ``-game``: в самом CSDK файлов игры нет, а в установленной игре — есть.
    """
    candidates: list[Path | None] = [getattr(ctx.args, "game_dir", None)]
    raw = getattr(ctx.args, "game_path", None)
    if raw:
        # --game-path мог не дойти до args.game_dir (шаг prepare его не требовал),
        # поэтому проверяем и исходный аргумент.
        try:
            candidates.append(paths_mod.find_game_dir(Path(raw)))
        except FileNotFoundError:
            pass
    if not any(candidates):
        try:
            candidates.append(paths_mod.find_game_dir(None))
        except FileNotFoundError:  # pragma: no cover - защитная ветка
            candidates.append(None)
    for candidate in candidates:
        if not candidate:
            continue
        citadel = Path(candidate) / "game" / "citadel"
        if (citadel / "gameinfo.gi").is_file():
            return citadel
    return None


def _compile_attempts(ctx: Context, resolver: "_CompilerResolver") -> list[_CompileAttempt]:
    """Как пробовать компиляцию, по порядку.

    1. Без ``-game``: так собирают все известные пайплайны сообщества, и это
       единственный вариант, который работает на Reduced CSDK 12 (там нет
       ``game/citadel/gameinfo.gi``).
    2. С ``gameinfo.gi`` установленной игры: страховка для сборок CSDK, которые
       всё же требуют ``-game``. Результат всё равно забираем в ``build/``, а из
       дерева игры убираем (см. ``_compiled_roots``).

    Явный ``--compiler-game`` отменяет автоопределение: пользователь сам сказал,
    какой каталог передавать.
    """
    explicit = getattr(ctx.args, "compiler_game", None)
    if explicit:
        path = Path(explicit).expanduser()
        return [_CompileAttempt(f"-game {path}", path)]
    attempts = [_CompileAttempt("без -game (аддон определяется по пути исходника)")]
    game = _fallback_game_dir(ctx)
    if game is not None:
        attempts.append(_CompileAttempt(f"-game {game} (gameinfo.gi установленной игры)", game))
    return attempts


def _run_compiler(ctx: Context, command: list[str], resolver: "_CompilerResolver") -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        command,
        cwd=str(resolver.compiler.parent) if resolver.compiler else None,
        capture_output=True,
        text=True,
    )


def _gameinfo_hints() -> list[str]:
    """Что делать, если компилятор не нашёл ``gameinfo.gi`` (текст идёт в отчёт)."""
    return [
        "Причина похожа на «компилятор не понял, для какого мода собирать»: он искал",
        'game\\citadel\\gameinfo.gi, а в Reduced CSDK 12 файлов самой игры нет — там только',
        "инструменты, content/citadel_addons и game/citadel_addons. Аддон определяется по пути",
        "исходника внутри content/citadel_addons/<addon>, поэтому -game передавать не нужно.",
        "",
        "Что делать:",
        " • убедиться, что -game не задан (--compiler-game и -game в --compiler-cmd);",
        " • если конкретной сборке CSDK -game всё же нужен — указать каталог с gameinfo.gi:",
        '     --compiler-game "…\\Deadlock\\game\\citadel"',
        " • либо задать всю команду самому:",
        '     --compiler-cmd "«{compiler}» -nop4 -f -i {source}"',
        " • проверить, что исходники лежат внутри content/citadel_addons/<addon> (шаг prepare)",
        "   и что CSDK распакован целиком: python3 tools/csdk_setup.py --csdk PATH",
    ]


#: Сколько строк вывода компилятора показываем: начало, хвост и ключевые строки из середины.
_EXCERPT_HEAD = 20
_EXCERPT_TAIL = 15
_EXCERPT_KEY = 20
_ERROR_MARKERS = ("error", "fail", "unable", "can't", "cannot", "fatal", "exception", "warning", "ошиб")


def _output_excerpt(output: str) -> list[str]:
    """Выжимка из вывода компилятора, в которой не теряется начало.

    Раньше брался только хвост (последние 25 строк). Но первые строки вывода
    часто и есть причина: например, ошибка загрузки модуля компилятора. Поэтому
    показываем начало, хвост и уникальные строки с признаками ошибки из середины.
    """
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if len(lines) <= _EXCERPT_HEAD + _EXCERPT_TAIL + _EXCERPT_KEY:
        return lines
    middle = lines[_EXCERPT_HEAD:-_EXCERPT_TAIL]
    key = [line for line in dict.fromkeys(middle) if any(m in line.lower() for m in _ERROR_MARKERS)]
    key = key[:_EXCERPT_KEY]
    return (
        lines[:_EXCERPT_HEAD]
        + [f"… пропущено {len(middle)} строк; строки с признаками ошибки из середины:"]
        + key
        + ["… конец вывода:"]
        + lines[-_EXCERPT_TAIL:]
    )


def _csdk_damaged_hints() -> list[str]:
    """Что делать, если компилятор не смог загрузить собственный модуль (error 126)."""
    return [
        "Причина похожа на сломанную установку CSDK: resourcecompiler не смог загрузить свой",
        "модуль (modtools, «error 126» = не найдена DLL или её зависимость). Частые причины:",
        " • CSDK распакован не полностью или архив повреждён;",
        " • не хватает Microsoft Visual C++ Redistributable (x64) — одна из типичных причин 126;",
        " • CSDK лежит в OneDrive/Desktop/Documents, где антивирус или синхронизация мешают.",
        "",
        "Что делать:",
        r" • переименовать или удалить каталог %LOCALAPPDATA%\DeadlockModding\Reduced_CSDK_12:",
        "   поиск CSDK берёт его первым, даже если он сломан; затем запустить сборку с",
        "   --csdk-redownload (архив скачается заново, а не возьмётся из кэша);",
        " • установить VC++ Redistributable 2015–2022 (x64) и повторить сборку;",
        " • журнал полностью: папка build\\compile_logs (см. ссылки ниже).",
    ]


def _is_csdk_damaged(text: str) -> bool:
    lowered = text.lower()
    return "unable to load module" in lowered or "error 126" in lowered


def _compiler_failure_message(
    ctx: Context,
    relative: Path,
    result: "subprocess.CompletedProcess[str]",
    command: list[str],
    attempts: list[_CompileAttempt],
) -> str:
    """Понятный текст ошибки компиляции вместо одного кода возврата."""
    lines = [
        f"resourcecompiler вернул код {result.returncode} для {relative}",
        f"  команда: {shlex.join(command)}",
    ]
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if output:
        lines += ["  вывод компилятора (последняя попытка):", "\n".join(f"    {line}" for line in _output_excerpt(output))]
    if len(attempts) > 1:
        lines.append("  пробовал варианты: " + ", ".join(attempt.label for attempt in attempts))
    for entry in _attempt_logs_for(ctx, relative):
        lines.append(f"  журнал попытки «{entry['attempt']}» (код {entry['returncode']}): {entry['log']}")
    if _gameinfo_trouble(result):
        lines.append("")
        lines += [f"  {line}" if line else "" for line in _gameinfo_hints()]
    if _is_csdk_damaged(output):
        lines.append("")
        lines += [f"  {line}" if line else "" for line in _csdk_damaged_hints()]
    return "\n".join(lines)


def _attempt_logs_for(ctx: Context, relative: Path) -> list[dict]:
    """Записи журнала по каждой попытке компиляции именно этого исходника."""
    return [
        entry for entry in ctx.state.get("compile_logs", [])
        if entry.get("source") == relative.as_posix()
    ]


def _record_compile_attempt(
    ctx: Context,
    relative: Path,
    attempt: "_CompileAttempt",
    command: list[str],
    result: "subprocess.CompletedProcess[str]",
) -> None:
    """Сохранить полный вывод попытки в ``build/compile_logs`` и запомнить выжимку.

    Полный журнал нужен, потому что в отчёт и консоль попадает только выжимка.
    Файлы лежат в build/ (он в .gitignore).
    """
    index = len(_attempt_logs_for(ctx, relative)) + 1
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", relative.as_posix())
    path = ctx.build_dir / "compile_logs" / f"{slug}.{index}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([
        f"# исходник: {relative.as_posix()}",
        f"# вариант: {attempt.label}",
        f"# команда: {shlex.join(command)}",
        f"# код возврата: {result.returncode}",
        "",
        "## stdout",
        result.stdout or "",
        "",
        "## stderr",
        result.stderr or "",
    ])
    path.write_text(text, encoding="utf-8")
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    ctx.state.setdefault("compile_logs", []).append({
        "source": relative.as_posix(),
        "attempt": attempt.label,
        "returncode": result.returncode,
        "command": shlex.join(command),
        "log": _rel(path, ctx.root),
        "excerpt": _output_excerpt(output),
    })


def _stat_signature(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def step_compile(ctx: Context) -> None:
    """Скомпилировать исходники аддона в ``*_c``."""
    log("[2/4] compile — resourcecompiler")
    sources = sorted(path for path in ctx.content_root.rglob("*") if path.is_file())
    if not sources:
        raise SystemExit("нет дерева аддона: сначала выполни шаг prepare")

    # CSDK ищем здесь, а не в main: до шага prepare сборка может упасть по
    # другой причине, и качать гигабайтный архив было бы незачем.
    _ensure_csdk(ctx)
    resolver = _resolve_compiler(ctx)
    if resolver.compiler is None and not ctx.args.compiler_cmd:
        if not ctx.args.allow_uncompiled:
            ctx.state["compile_skipped"] = True
            ctx.warn(
                "не найден CSDK с resourcecompiler.exe — сборка остановлена на шаге compile.\n"
                "  Варианты:\n"
                "   • разрешить автоустановку (по умолчанию включена, отключается --no-auto-csdk):\n"
                "     сборщик сам скачает и распакует Reduced CSDK 12;\n"
                "   • скачать CSDK 12 (https://deadlockmodding.pages.dev/modding-tools/csdk-12) и передать --csdk PATH;\n"
                "   • собрать только исходники для проверки: --allow-uncompiled."
            )
            raise _NeedsCompile("нет компилятора CSDK")
        ctx.state["uncompiled"] = True
        ctx.warn(
            "компилятор не найден: собираю артефакты «только исходники».\n"
            "  Движок Deadlock НЕ загружает сырые .xml/.css из аддона — такой VPK не работает в игре\n"
            "  и пакет для Deadlock Mod Manager в этом режиме не собирается."
        )
        _copy_tree(ctx.content_root, ctx.output_root)
        _write_state(ctx)
        return

    if not ctx.args.compiler_cmd:
        # Реальный CSDK: исходники должны лежать в его дереве content/, а каталог
        # под результат — существовать (так же делают пайплайны сообщества).
        _copy_tree(ctx.content_root, resolver.content_root)
        if resolver.output_root != ctx.output_root:
            resolver.output_root.mkdir(parents=True, exist_ok=True)
    if ctx.args.compiler_cmd:
        ctx.note(f"шаблон команды компиляции: {ctx.args.compiler_cmd}")
    else:
        ctx.note(f"компилятор: {resolver.compiler}")
    if resolver.content_root != ctx.content_root:
        ctx.note(f"исходники выложены в {resolver.content_root}")

    game_dir = _fallback_game_dir(ctx)
    attempts = _compile_attempts(ctx, resolver)
    for attempt in attempts:
        if attempt.game is None:
            continue
        gameinfo = attempt.game if attempt.game.is_file() else attempt.game / "gameinfo.gi"
        if not gameinfo.is_file():
            ctx.warn(
                f"в «{attempt.game}» нет gameinfo.gi — resourcecompiler обычно падает на этом "
                'с «Can\'t find \'game\\citadel\\gameinfo.gi\'». Если сборка не нуждается в -game, '
                "убери --compiler-game."
            )
    _note_attempts(ctx, attempts)
    ctx.state["compile_attempts"] = [attempt.label for attempt in attempts]

    roots = _compiled_roots(ctx, resolver, game_dir=game_dir)
    # Снимок «до компиляции»: по нему отличаем свежий вывод от чужого файла с тем же именем.
    before = {
        root.root / relative.with_suffix(ext): _stat_signature(root.root / relative.with_suffix(ext))
        for root in roots
        for relative in (source.relative_to(ctx.content_root) for source in sources)
        if (ext := COMPILED_EXTENSIONS.get(relative.suffix.lower())) is not None
    }

    produced: list[Path] = []
    for source in sources:
        relative = source.relative_to(ctx.content_root)
        expected_ext = COMPILED_EXTENSIONS.get(relative.suffix.lower())
        result, command, used = _compile_one(ctx, source, relative, resolver, attempts)
        if result.returncode != 0:
            message = _compiler_failure_message(ctx, relative, result, command, attempts)
            final_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            ctx.state["compile_error"] = {
                "source": relative.as_posix(),
                "returncode": result.returncode,
                "command": shlex.join(command),
                "output": _output_excerpt(final_output),
                "attempts": [attempt.label for attempt in attempts],
                "attempt_logs": _attempt_logs_for(ctx, relative),
                "hints": (
                    (_gameinfo_hints() if _gameinfo_trouble(result) else [])
                    + (_csdk_damaged_hints() if _is_csdk_damaged(final_output) else [])
                ) or None,
            }
            raise SystemExit(message)
        if used is not None and used.game is not None and not ctx.state.get("compiler_game"):
            ctx.state["compiler_game"] = str(used.game)
        if expected_ext is None:
            continue
        produced.append(_take_compiled(ctx, relative, expected_ext, roots, before, source))

    if not produced:
        raise SystemExit("компиляция не дала ни одного ресурса")
    ctx.state["compiled"] = [_rel(path, ctx.build_dir) for path in produced]
    _write_state(ctx)


def _note_attempts(ctx: Context, attempts: list[_CompileAttempt]) -> None:
    """Записать в отчёт, какими вызовами будем компилировать."""
    if ctx.args.compiler_cmd:
        return
    if len(attempts) == 1:
        if attempts[0].game is not None:
            ctx.note(
                f"вызов компилятора: -game {attempts[0].game} -nop4 -f -i <файл> "
                "(каталог с gameinfo.gi задан через --compiler-game)"
            )
            return
        ctx.note(
            "вызов компилятора: -nop4 -f -i <файл>, без -game "
            "(аддон определяется по пути исходника в content/citadel_addons/<addon>)"
        )
        ctx.note(
            "запасной вариант при ошибке с gameinfo.gi: --compiler-game "
            '"<Deadlock>\\game\\citadel"'
        )
        return
    ctx.note(
        "вызов компилятора: -nop4 -f -i <файл>; если CSDK потребует gameinfo.gi — "
        f"повторю с -game {attempts[1].game}"
    )


def _compile_one(
    ctx: Context,
    source: Path,
    relative: Path,
    resolver: "_CompilerResolver",
    attempts: list[_CompileAttempt],
) -> tuple["subprocess.CompletedProcess[str]", list[str], _CompileAttempt | None]:
    """Прогнать исходник через компилятор, перебирая варианты вызова.

    Возвращает результат последней попытки, её команду и вариант, на котором
    получилось (``None`` — успеха не было). Переход к следующему варианту бывает
    только при ошибке про ``gameinfo.gi``: остальные ошибки повторять бессмысленно.
    """
    result: subprocess.CompletedProcess[str] | None = None
    command: list[str] = []
    for index, attempt in enumerate(attempts):
        next_command = _compiler_command(ctx, source, resolver, game=attempt.game)
        if index and next_command == command:
            # Вариант отличается только подписью (например, в --compiler-cmd нет
            # плейсхолдера {game}) — повторять тот же вызов незачем.
            continue
        command = next_command
        result = _run_compiler(ctx, command, resolver)
        _record_compile_attempt(ctx, relative, attempt, command, result)
        if result.returncode == 0:
            if index:
                ctx.warn(
                    f"{relative.as_posix()}: компилятор не взялся за работу без -game, "
                    f"помог вариант «{attempt.label}»"
                )
            return result, command, attempt
        next_attempt = attempts[index + 1] if index + 1 < len(attempts) else None
        if next_attempt is not None and _gameinfo_trouble(result):
            ctx.warn(
                f"{relative.as_posix()}: компилятор не нашёл gameinfo.gi — "
                f"пробую «{next_attempt.label}»"
            )
            continue
        break
    assert result is not None
    return result, command, None


def _take_compiled(
    ctx: Context,
    relative: Path,
    expected_ext: str,
    roots: list[_CompiledRoot],
    before: dict[Path, tuple[int, int] | None],
    source: Path,
) -> Path:
    """Найти скомпилированный ресурс, забрать его в ``build/game/...`` и вернуть путь."""
    compiled_name = relative.with_suffix(expected_ext)
    target = ctx.output_root / compiled_name
    found: Path | None = None
    for root in roots:
        candidate = root.root / compiled_name
        signature = _stat_signature(candidate)
        if signature is None or signature[1] == 0:
            continue
        if root.fresh_only and signature == before.get(candidate):
            continue
        found = candidate
        if candidate != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            ctx.note(f"ресурс взят из «{root.label}»: {_rel(candidate, root.root)}")
        if root.cleanup and before.get(candidate) is None:
            _remove_quietly(candidate, stop_at=root.root)
        break
    if found is None:
        raise SystemExit(
            f"компилятор не создал {compiled_name.as_posix()} — проверь CSDK и входные файлы.\n"
            "  Если твой компилятор кладёт результат в нестандартное место, укажи его через --compiled-from."
        )
    if source.stat().st_mtime_ns > target.stat().st_mtime_ns:
        ctx.warn(
            f"ресурс {compiled_name.as_posix()} старше исходника {relative.as_posix()} — "
            "возможно, компиляция его не обновила"
        )
    ctx.note(f"скомпилировано: {_rel(target, ctx.build_dir)} ({target.stat().st_size} Б)")
    return target


def _remove_quietly(path: Path, *, stop_at: Path) -> None:
    """Удалить файл и опустевшие каталоги, не поднимаясь выше ``stop_at``."""
    try:
        path.unlink()
    except OSError:
        return
    parent = path.parent
    while parent != stop_at and stop_at in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _pack_source_tree(ctx: Context) -> Path:
    """Найти дерево, которое надо упаковать.

    Обычно это стадия ``build/game/...`` после шага compile. Но шаги можно
    запускать и по отдельности, поэтому проверяем ещё ``--compiled-from`` и
    дерево CSDK — так работает сборка VPK из того, что скомпилировано вручную
    в интерфейсе CSDK.
    """
    candidates: list[tuple[Path, str]] = [(ctx.output_root, "стадия сборки")]
    if ctx.args.compiled_from:
        candidates.append((Path(ctx.args.compiled_from).expanduser().resolve(), "--compiled-from"))
    # Явно указанный --csdk доверяем как есть: упаковать дерево, скомпилированное
    # в интерфейсе CSDK, можно и без resourcecompiler.exe на этой машине.
    csdk = (Path(ctx.args.csdk).expanduser().resolve() if ctx.args.csdk
            else paths_mod.find_csdk(None))
    if csdk is not None:
        candidates.append((csdk / "game" / "citadel_addons" / ctx.addon_name, "дерево CSDK"))
    for root, label in candidates:
        if root.is_dir() and any(path.is_file() for path in root.rglob("*")):
            if label != "стадия сборки":
                ctx.note(f"ресурсы взяты из «{label}»: {_rel(root, ctx.root)}")
            if not (root / "panorama").is_dir():
                ctx.warn(f"в «{label}» нет каталога panorama — проверь, что это дерево аддона")
            ctx.state.setdefault("packed_from", str(root))
            return root

    if ctx.args.allow_uncompiled:
        # Режим «только исходники»: пакуем дерево content как есть. В игре это
        # не заработает (движку нужны *_c), но пайплайн и упаковку проверить можно.
        ctx.state["uncompiled"] = True
        ctx.warn(
            "нет скомпилированного дерева: собираю VPK «только исходники».\n"
            "  Движок Deadlock НЕ загружает сырые .xml/.css из аддона — такой VPK в игре не работает\n"
            "  и пакет для Deadlock Mod Manager в этом режиме не собирается."
        )
        return ctx.content_root

    raise SystemExit(
        "нет дерева для упаковки: выполни шаг compile, укажи --compiled-from или --csdk,\n"
        "  либо собери пайплайн без компиляции: --allow-uncompiled (в игре не заработает)"
    )


def step_pack(ctx: Context) -> None:
    """Упаковать ресурсы в VPK и вшить метаданные."""
    log("[3/4] pack — VPK + метаданные")
    tree_root = _pack_source_tree(ctx)

    payloads: dict[str, bytes] = {}
    for path in sorted(tree_root.rglob("*")):
        if path.is_file():
            payloads[path.relative_to(tree_root).as_posix()] = path.read_bytes()
    if not payloads:
        raise SystemExit("нечего паковать: дерево аддона пустое")

    raw = vpk.build_vpk(payloads)
    problems = vpk.verify_vpk(raw, payloads)
    if problems:
        raise SystemExit("собранный VPK не прошёл проверку:\n  - " + "\n  - ".join(problems))
    ctx.note(f"VPK собран: {len(payloads)} записей, {len(raw)} Б")

    imprinted, identity = modinfo.imprint(
        raw,
        title=ctx.args.title or MOD_TITLE,
        author=MOD_AUTHOR,
        version=ctx.version,
        description=MOD_DESCRIPTION,
        tool=TOOL,
        tool_version=TOOL_VERSION,
    )
    problems = modinfo_readback_problems(imprinted, payloads)
    if problems:
        raise SystemExit("метаданные не сошлись:\n  - " + "\n  - ".join(problems))

    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    target = ctx.vpk_path if not ctx.sources_only else ctx.out_dir / "sources-only" / ctx.vpk_path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(imprinted)

    ctx.state["vpk"] = _rel(target, ctx.root)
    ctx.state["vpk_size"] = len(imprinted)
    ctx.state["vpk_sha256"] = vpk.sha256_of(imprinted)
    ctx.state["vpk_entries"] = sorted(vpk.normalize_virtual_path(name) for name in payloads)
    ctx.state["original_identity"] = identity.to_json()
    ctx.note(f"файл: {_rel(target, ctx.root)} ({len(imprinted)} Б, sha256 {ctx.state['vpk_sha256'][:16]}…)")
    ctx.note(f"канонический identity (до прошивки): {identity.sha256[:16]}…")
    _write_state(ctx)


def modinfo_readback_problems(vpk_bytes: bytes, payloads: dict[str, bytes]) -> list[str]:
    """Проверить, что после прошивки все игровые файлы на месте, а embed читается."""
    problems: list[str] = []
    archive = vpk.VpkArchive(Path("<memory>"), vpk_bytes)
    names = set(archive.list_entries())
    for name in payloads:
        canonical = vpk.normalize_virtual_path(name)
        if canonical not in names:
            problems.append(f"после прошивки потеряна запись {canonical}")
            continue
        entry = archive.find(canonical)
        assert entry is not None
        if archive.read_entry(entry) != payloads[name]:
            problems.append(f"после прошивки изменилось содержимое {canonical}")
    for required in (modinfo.ADDONINFO_ENTRY, modinfo.MODINFO_ENTRY):
        if vpk.normalize_virtual_path(required) not in names:
            problems.append(f"нет записи {required}")
    embed = modinfo.read_embed(vpk_bytes)
    if embed is None:
        problems.append("метаданные не читаются обратно")
    return problems


def step_dmm(ctx: Context) -> None:
    """Собрать пакет для Deadlock Mod Manager и zip для перетаскивания."""
    log("[4/4] dmm — пакет для Deadlock Mod Manager")
    if ctx.sources_only:
        ctx.warn("режим «только исходники»: пакет для DMM не собирается (мод не заработает в игре)")
        return
    if not ctx.state.get("vpk"):
        raise SystemExit("нет собранного VPK: выполни шаг pack")

    vpk_path = ctx.root / ctx.state["vpk"]
    bundle_file = dmm_bundle.BundleFile(name=f"{ctx.addon_name}_dir.vpk", source=vpk_path)
    document = dmm_bundle.build_document(
        files=[bundle_file],
        entry_folder=dmm_bundle.slug(f"0000-{ctx.addon_name}"),
        title=ctx.args.title or MOD_TITLE,
        author=MOD_AUTHOR,
        description=MOD_DESCRIPTION,
        category=MOD_CATEGORY,
        link=ctx.args.link or None,
        enabled=True,
        order=0,
        manager_version=TOOL_VERSION,
        created_at=_iso_now(),
    )
    manifest = dmm_bundle.write_bundle(ctx.bundle_dir, document, [bundle_file])
    problems = dmm_bundle.verify_bundle(ctx.bundle_dir)
    if problems:
        raise SystemExit("пакет для DMM не прошёл проверку:\n  - " + "\n  - ".join(problems))
    ctx.note(f"пакет: {_rel(ctx.bundle_dir, ctx.root)}/{manifest.name}")

    dmm_bundle.write_zip(ctx.zip_path, ctx.bundle_dir, notes=_install_notes(ctx))
    ctx.state["bundle"] = _rel(ctx.bundle_dir, ctx.root)
    ctx.state["bundle_zip"] = _rel(ctx.zip_path, ctx.root)
    ctx.state["bundle_summary"] = dmm_bundle.bundle_summary(ctx.bundle_dir)
    ctx.note(f"архив для перетаскивания в DMM: {_rel(ctx.zip_path, ctx.root)}")
    _write_state(ctx)


def step_report(ctx: Context) -> None:
    """Отчёт о сборке и контрольные суммы артефактов."""
    log("[report] отчёт и контрольные суммы")
    ctx.state["report_written"] = True
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = [
        path for path in sorted(ctx.out_dir.rglob("*"))
        if path.is_file() and path.name not in ("BUILD_REPORT.md", "SHA256SUMS.txt")
    ]
    lines = [f"{vpk.sha256_of_file(path)}  {_rel(path, ctx.out_dir)}" for path in artifacts]
    (ctx.out_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.state["artifacts"] = {_rel(path, ctx.out_dir): vpk.sha256_of_file(path) for path in artifacts}

    report = _render_report(ctx)
    (ctx.out_dir / "BUILD_REPORT.md").write_text(report, encoding="utf-8")
    ctx.note(f"отчёт: {_rel(ctx.out_dir / 'BUILD_REPORT.md', ctx.root)}")
    _write_state(ctx)


# --------------------------------------------------------------------------- #
# отчёты и состояние
# --------------------------------------------------------------------------- #


class _NeedsCompile(Exception):
    """Сборка дошла до компиляции и остановилась: нужен CSDK."""


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _write_state(ctx: Context) -> None:
    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "tool": TOOL,
        "toolVersion": TOOL_VERSION,
        "addon": ctx.addon_name,
        "version": ctx.version,
        "updatedAt": _iso_now(),
        "gitCommit": _git_commit(ctx.root),
        "notes": ctx.notes,
        "warnings": ctx.warnings,
        **{key: value for key, value in ctx.state.items() if key != "bundle_summary"},
    }
    (ctx.build_dir / "build_state.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _install_notes(ctx: Context) -> str:
    return (
        "Deadlock :: Bhop Trainer — пакет для Deadlock Mod Manager\n"
        "========================================================\n\n"
        "Способ 1 (пакет):  в DMM «Import → Open interchange bundle» → выбери\n"
        f"  {dmm_bundle.MANIFEST_FILENAME} из распакованного архива.\n\n"
        "Способ 2 (перетаскивание):  распакуй архив и перетащи .vpk в раздел «Add Mods»\n"
        "  в Deadlock Mod Manager.\n\n"
        "Дальше: закрой игру → «Launch modded» (DMM сам правит gameinfo.gi) → панель в HUD.\n\n"
        f"Подробности и разбор проблем: см. BUILD_REPORT.md (версия {ctx.version}).\n"
    )


def _report_status(ctx: Context) -> str:
    """Честный статус сборки: отчёт пишется и когда сборка не дошла до конца.

    Раньше статус смотрел только на ``sources_only`` и потому врал: упавшая на
    компиляции сборка отчитывалась «готов к установке» (VPK-то не было).
    """
    if ctx.errors:
        if ctx.state.get("compile_error"):
            return "ошибка компиляции (VPK не собран)"
        return "ошибка сборки (VPK не собран)"
    if ctx.state.get("compile_skipped"):
        return "остановлено на компиляции: нет CSDK (VPK не собран)"
    if ctx.sources_only:
        return "только исходники (в игре не заработает)"
    if not ctx.state.get("vpk"):
        return "VPK не собран"
    if not ctx.state.get("bundle"):
        return "VPK собран, пакет для DMM — нет"
    return "готов к установке"


def _compile_notes(ctx: Context) -> list[str]:
    """Строки про компиляцию для отчёта: чем собирали и что из этого вышло."""
    args = ctx.args
    if args.compiler_cmd:
        return [
            f"- Компиляция подменена шаблоном `--compiler-cmd`: `{args.compiler_cmd}` — "
            "`*_c` создаёт не `resourcecompiler.exe`, а эта команда.",
        ]
    if ctx.state.get("uncompiled") or ctx.state.get("compile_skipped"):
        return [
            "- Компиляция не выполнялась: артефакты содержат сырые `.xml`/`.vcss`, "
            "движок Deadlock их не грузит.",
        ]
    compiler = ctx.state.get("csdk_compiler") or "resourcecompiler.exe"
    notes = [
        f"- Компиляцию запускал `resourcecompiler.exe` из CSDK на этой машине "
        f"(`{sys.platform}`): `{compiler}`.",
        "- Вызов — `resourcecompiler.exe -nop4 -f -i <исходник>`, как в пайплайнах сообщества "
        "(QOLLOCK, Deadlock QoL HUD Mod, Deadlock Stats Editor).",
        "- `-game` по умолчанию не передаётся: аддон определяется по пути исходника внутри "
        "`content/citadel_addons/<addon>`.",
    ]
    if ctx.state.get("compiler_game"):
        notes.append(
            f"- Использован `-game {ctx.state['compiler_game']}` (запасной вариант: без него "
            "компилятор не нашёл `gameinfo.gi`)."
        )
    compile_error = ctx.state.get("compile_error")
    if compile_error:
        notes.append(
            f"- Компиляция упала на `{compile_error.get('source')}` (код {compile_error.get('returncode')})."
        )
    return notes


def _render_report(ctx: Context) -> str:
    status = _report_status(ctx)
    lines = [
        "# Отчёт сборки: Deadlock :: Bhop Trainer",
        "",
        f"- Версия мода: **{ctx.version}**",
        f"- Инструмент: `{TOOL}` {TOOL_VERSION}",
        f"- Когда собрано: {_iso_now()}",
        f"- Коммит: `{_git_commit(ctx.root) or 'нет данных'}`",
        f"- Статус: **{status}**",
        "",
        "## Что получилось",
        "",
    ]
    if ctx.state.get("vpk"):
        lines.append(f"- VPK: `{ctx.state['vpk']}` — {ctx.state.get('vpk_size', 0)} Б, "
                     f"sha256 `{ctx.state.get('vpk_sha256', '')[:32]}…`")
        lines.append(f"- Записей в VPK: {len(ctx.state.get('vpk_entries', []))}")
        entries = ctx.state.get("vpk_entries", [])
        for entry in entries:
            lines.append(f"  - `{entry}`")
    else:
        lines.append("- VPK не собирался")
    if ctx.state.get("bundle"):
        lines.append(f"- Пакет для DMM: `{ctx.state['bundle']}`")
        summary = ctx.state.get("bundle_summary", {})
        lines.append(f"  - ключ: `{summary.get('key')}`, localId: `{summary.get('localId')}`")
    if ctx.state.get("bundle_zip"):
        lines.append(f"- Архив для перетаскивания: `{ctx.state['bundle_zip']}`")

    lines += [
        "",
        "## Входные данные",
        "",
        f"- Ванильный hud.xml: {ctx.state.get('hud_source_label', 'нет данных')}",
        f"  - источник: `{ctx.state.get('hud_source_origin', 'нет данных')}`"
        + (f", ссылка `{ctx.state['hud_source_url']}`" if ctx.state.get("hud_source_url") else ""),
        f"  - сборка игры, к которой относится файл: `{ctx.state.get('hud_build_id', 'неизвестно')}`"
        + (f", установленная сборка: `{ctx.state['hud_game_build_id']}`"
           if ctx.state.get("hud_game_build_id") else ""),
        f"  - sha256 оригинала: `{ctx.state.get('hud_vanilla_sha256', '')}`",
        f"  - sha256 после патча: `{ctx.state.get('hud_patched_sha256', '')}`",
        f"- Компилятор CSDK: `{ctx.state.get('csdk_compiler') or 'не найден'}`",
        f"  - статус: `{ctx.state.get('csdk_status', 'не потребовался')}`"
        + (f", каталог `{ctx.state['csdk_root']}`" if ctx.state.get("csdk_root") else ""),
        f"- Сниппет `src/bhop_trainer.vxml`: sha256 `{ctx.state.get('snippet_sha256', '')[:32]}…`",
        f"- Стили `src/bhop_trainer.vcss`: sha256 `{ctx.state.get('styles_sha256', '')[:32]}…`",
        "",
        "## Установка через Deadlock Mod Manager",
        "",
        f"1. Открой `{ctx.state.get('bundle', 'dist/dmm-bundle')}` (или распакуй `{ctx.state.get('bundle_zip', 'dist/*.zip')}`).",
        "2. В DMM: **Import → Open interchange bundle** → выбери `mod-interchange.json`.",
        "   Альтернатива: перетащи zip в раздел **Add Mods**.",
        "3. Закрой Deadlock и нажми **Launch modded** — DMM сам добавит `Game citadel/addons` в `gameinfo.gi`.",
        "4. Проверка: `python3 tools/install_dmm.py verify` — покажет, лежит ли мод в `citadel/addons` и включён ли путь.",
        "",
        "## Заметки сборки",
        "",
    ]
    lines += [f"- {note}" for note in ctx.notes] or ["- (нет)"]
    if ctx.warnings:
        lines += ["", "## Предупреждения", ""] + [f"- {warning}" for warning in ctx.warnings]

    compile_error = ctx.state.get("compile_error")
    if ctx.errors or compile_error:
        lines += ["", "## Ошибки", ""]
        if compile_error:
            # Ошибка компиляции разбирается по частям: сообщение в логе то же
            # самое, но в отчёте удобнее команда и вывод компилятора отдельно.
            lines += [
                f"- `resourcecompiler` вернул код `{compile_error.get('returncode')}` для "
                f"`{compile_error.get('source')}` — `*_c` не создан.",
                f"- Команда: `{compile_error.get('command')}`",
            ]
            if compile_error.get("attempts"):
                lines.append(
                    "- Варианты вызова: "
                    + "; ".join(f"`{label}`" for label in compile_error["attempts"])
                )
            # Каждая попытка — со своей выжимкой и ссылкой на полный журнал: первая
            # попытка может падать по другой причине, чем последняя.
            for entry in compile_error.get("attempt_logs") or []:
                lines += [
                    "",
                    f"Попытка «{entry['attempt']}» — код {entry['returncode']}, "
                    f"полный журнал: `{entry['log']}`",
                    "",
                    "```text",
                    *entry["excerpt"],
                    "```",
                ]
            if not compile_error.get("attempt_logs") and compile_error.get("output"):
                lines += ["", "Вывод `resourcecompiler`:", "", "```text", *compile_error["output"], "```"]
            if compile_error.get("hints"):
                lines += [""] + list(compile_error["hints"])
        else:
            lines += [f"- {error}" for error in ctx.errors]

    lines += [
        "",
        "## Что делал сборщик и чего в отчёте нет",
        "",
        *(_compile_notes(ctx)),
        "- Патч `hud.xml` идемпотентен: повторная сборка на том же ванильном файле даёт то же содержимое.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

STEPS: dict[str, Callable[[Context], None]] = {
    "prepare": step_prepare,
    "compile": step_compile,
    "pack": step_pack,
    "dmm": step_dmm,
    "report": step_report,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_mod.py",
        description="Автосборщик VPK и пакета Deadlock Mod Manager для Bhop Trainer HUD.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--game-path",
        help="папка Deadlock (или её game/citadel) — из неё берётся ванильный hud.xml",
    )
    parser.add_argument("--hud-xml", help="явный путь к ванильному panorama/layout/hud.xml")
    parser.add_argument(
        "--hud-xml-url",
        help="своя ссылка на ванильный hud.xml: http(s) или file:///… "
             "(по умолчанию — зеркало депо GameTracking)",
    )
    parser.add_argument(
        "--no-hud-download",
        dest="hud_download",
        action="store_false",
        help="не скачивать ванильный hud.xml: брать только файл на диске, дерево игры/CSDK и кэш",
    )
    parser.add_argument(
        "--refresh-hud-xml",
        action="store_true",
        help="не использовать кэш: взять ванильный hud.xml заново (после патча игры)",
    )
    parser.add_argument("--csdk", help="путь к CSDK с resourcecompiler.exe")
    parser.add_argument(
        "--no-auto-csdk",
        dest="auto_csdk",
        action="store_false",
        help="не скачивать CSDK автоматически, если он не найден",
    )
    parser.add_argument("--csdk-install-dir", help="куда распаковать скачанный CSDK (по умолчанию — вне OneDrive)")
    parser.add_argument("--csdk-cache-dir", help="куда сложить скачанный архив CSDK")
    parser.add_argument("--csdk-url", help="своя ссылка на архив CSDK (по умолчанию — со страницы CSDK 12)")
    parser.add_argument("--csdk-sha256", help="контрольная сумма архива CSDK")
    parser.add_argument("--csdk-redownload", action="store_true", help="скачать CSDK заново, игнорируя кэш")
    parser.add_argument(
        "--no-tool-install",
        action="store_true",
        help="не ставить 7-Zip через winget (нужен, если архив CSDK в .7z/.rar)",
    )
    parser.add_argument(
        "--compiler-game",
        help="каталог с gameinfo.gi (или сам файл) для -game — нужен только тем сборкам CSDK, "
             "которые не компилируют без него; по умолчанию -game не передаётся",
    )
    parser.add_argument(
        "--compiler-cmd",
        help="своя команда компиляции; плейсхолдеры: {compiler} {sdk} {game} {addon} {source} {content_root} {output_root}",
    )
    parser.add_argument("--steps", default="prepare,compile,pack,dmm,report",
                        help="какие шаги выполнять (по умолчанию все)")
    parser.add_argument("--out", default=None, help="каталог артефактов (по умолчанию dist)")
    parser.add_argument("--build-dir", default=None, help="рабочий каталог (по умолчанию build)")
    parser.add_argument("--addon-name", default="bhop_trainer", help="имя аддона: строчные латинские буквы, цифры, _")
    parser.add_argument("--title", default=None, help="название мода в метаданных и пакете")
    parser.add_argument("--version", default="1.0.0", help="версия мода")
    parser.add_argument("--link", default=None, help="ссылка на страницу мода (необязательно)")
    parser.add_argument(
        "--compiled-from",
        help="каталог, где компилятор оставил *_c (если он не совпадает с build/game или CSDK)",
    )
    parser.add_argument("--allow-uncompiled", action="store_true",
                        help="собрать без CSDK: VPK только с исходниками (в игре не работает, пакет DMM не собирается)")
    parser.add_argument("--clean", action="store_true",
                        help="собрать с нуля: удалить build/ и каталог артефактов перед стартом")
    parser.add_argument("--json", action="store_true", help="напечатать итог в JSON")
    return parser


def _validate_addon_name(name: str) -> None:
    if not re.fullmatch(r"[a-z0-9_]{2,40}", name):
        raise SystemExit("--addon-name должен состоять из строчных латинских букв, цифр и _")


def _wipe_dir(path: Path) -> None:
    """Удалить рабочий каталог целиком (для ``--clean``), но не корень диска."""
    path = Path(path)
    if not path.is_dir() or path.resolve() == Path(path.anchor):
        return
    shutil.rmtree(path)


def _ensure_csdk(ctx: Context) -> None:
    """Шаг 0: найти CSDK, а если его нет — скачать и распаковать.

    Раньше на этом месте сборка останавливалась с кодом 3 и просила ставить
    CSDK руками. Теперь всё нужное докачивается само (``tools/csdk_setup.py``);
    отказаться можно флагом ``--no-auto-csdk``.
    """
    args = ctx.args
    if args.compiler_cmd or args.allow_uncompiled:
        return  # компилятор подменили или сборка и так «только исходники»
    csdk_setup.set_progress_stream(sys.stderr if _LOG_TO_STDERR else sys.stdout)
    result = csdk_setup.ensure_csdk(
        explicit=args.csdk,
        install_root=args.csdk_install_dir,
        cache_dir=args.csdk_cache_dir,
        url=args.csdk_url,
        sha256=args.csdk_sha256,
        allow_download=args.auto_csdk,
        redownload=args.csdk_redownload,
        allow_tool_install=not args.no_tool_install,
        log=log,
    )
    ctx.state["csdk_status"] = result.status
    if result.ok:
        # Дальше все шаги работают с ним как с обычным, явно указанным CSDK.
        args.csdk = str(result.root)
        ctx.state["csdk_root"] = str(result.root)
        ctx.state["csdk_compiler"] = str(result.compiler)
        for message in result.messages:
            ctx.note(message)
    else:
        for message in result.messages:
            ctx.warn(message)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    global _LOG_TO_STDERR
    _LOG_TO_STDERR = bool(args.json)

    root = Path(__file__).resolve().parents[1]
    out_dir = Path(args.out).resolve() if args.out else root / "dist"
    build_dir = Path(args.build_dir).resolve() if args.build_dir else root / "build"

    if args.clean:
        log("[clean] собираю с нуля: удаляю рабочие каталоги")
        _wipe_dir(build_dir)
        _wipe_dir(out_dir)

    ctx = Context(root=root, out_dir=out_dir, build_dir=build_dir,
                  addon_name=args.addon_name, version=args.version, args=args)

    steps: list[str] = []
    exit_code = EXIT_OK
    try:
        _validate_addon_name(args.addon_name)
        steps = [step.strip() for step in args.steps.split(",") if step.strip()]
        unknown = [step for step in steps if step not in STEPS]
        if unknown:
            raise SystemExit(f"неизвестные шаги: {', '.join(unknown)} (доступны: {', '.join(STEPS)})")

        args.game_dir = paths_mod.find_game_dir(Path(args.game_path)) if not args.hud_xml else None

        log(f"Автосборка {args.addon_name} v{args.version}")
        for step in steps:
            STEPS[step](ctx)
    except _NeedsCompile as error:
        ctx.warn(str(error))
        exit_code = EXIT_NEEDS_COMPILE
    except KeyboardInterrupt:
        log("прервано пользователем")
        return 130
    except SystemExit as error:
        message = getattr(error, "code", None) or str(error)
        if isinstance(message, str):
            log(f"ОШИБКА: {message}")
            ctx.errors.append(message)
        exit_code = EXIT_ERROR
    except FileNotFoundError as error:
        log(f"ОШИБКА: {error}")
        ctx.errors.append(str(error))
        exit_code = EXIT_ERROR
    except (vpk.VpkError, modinfo.ModInfoError, hud_patch.HudPatchError,
            dmm_bundle.BundleError, OSError, ValueError) as error:
        log(f"ОШИБКА: {error}")
        ctx.errors.append(str(error))
        exit_code = EXIT_ERROR

    # Отчёт нужен всегда, когда сборка дошла хоть до чего-то значимого — в том
    # числе при остановке на компиляции или ошибке. Если шаг уже отработал —
    # второй раз не пишем.
    if not ctx.state.get("report_written") and (
        ctx.state.get("vpk") or ctx.errors or exit_code == EXIT_NEEDS_COMPILE
    ):
        try:
            step_report(ctx)
        except OSError as error:
            log(f"не удалось записать отчёт: {error}")

    if ctx.errors:
        exit_code = EXIT_ERROR

    if args.json:
        print(json.dumps(
            {
                "status": {EXIT_OK: "ok", EXIT_NEEDS_COMPILE: "needs-compile", EXIT_ERROR: "error"}[exit_code],
                "version": ctx.version,
                "addon": ctx.addon_name,
                "sourcesOnly": ctx.sources_only,
                "state": ctx.state,
                "warnings": ctx.warnings,
                "errors": ctx.errors,
            },
            indent=2, ensure_ascii=False,
        ))
    elif exit_code == EXIT_OK:
        log("готово.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
