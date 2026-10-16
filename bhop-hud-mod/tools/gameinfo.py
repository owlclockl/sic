"""``gameinfo.gi``: проверка, включение и отключение пути ``citadel/addons``.

Без строки ``Game citadel/addons`` в блоке ``SearchPaths`` игра игнорирует любые
моды из ``game/citadel/addons`` — это причина №1 «мод поставил, а его нет».

Здесь только минимальная правка: одна строка (плюс строковый маркер-комментарий
над ней) в существующем блоке ``SearchPaths``. Полную подмену блока делает сам
Deadlock Mod Manager (``MODDED_SEARCH_PATHS``) — см. файл в его исходниках.

Каждая запись сопровождается резервной копией ``gameinfo.gi.bhop-bak-<время>``,
а перед записью файл проверяется на баланс фигурных скобок: сломанный
``gameinfo.gi`` не даёт игре запуститься вообще.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trace_log  # noqa: E402

MARKER = "// bhop-trainer: Game citadel/addons"
#: Строка пути; отступ берётся у соседней ``Game citadel``, чтобы правка выглядела
#: родной в любом оформлении файла.
ADDONS_LINE = "Game                citadel/addons"
ADDONS_RE = re.compile(r"^\s*Game\s+citadel/addons\s*$", re.IGNORECASE | re.MULTILINE)
CITADEL_GAME_RE = re.compile(r"^\s*Game\s+citadel\s*$", re.IGNORECASE | re.MULTILINE)
DMM_MARKER = "// Deadlock Mod Manager - Start"


class GameInfoError(Exception):
    """Правка gameinfo.gi невозможна или опасна."""


@dataclass
class GameInfoStatus:
    path: Path
    exists: bool
    has_addons_path: bool = False
    braces_balanced: bool = True
    brace_delta: int = 0
    ours: bool = False
    managed_by_dmm: bool = False
    backups: list[Path] = field(default_factory=list)

    def lines(self) -> list[str]:
        if not self.exists:
            return [f"gameinfo.gi не найден: {self.path}"]
        out = [f"gameinfo.gi: {self.path}"]
        out.append(f"  путь citadel/addons: {'есть' if self.has_addons_path else 'НЕТ (моды не загрузятся)'}")
        out.append(f"  наши правки: {'есть' if self.ours else 'нет'}")
        if self.managed_by_dmm:
            out.append("  правки Deadlock Mod Manager: есть (маркеры DMM на месте)")
        out.append(
            "  фигурные скобки: "
            + ("сбалансированы" if self.braces_balanced else f"НЕ сбалансированы (разница {self.brace_delta:+d})")
        )
        if self.backups:
            out.append(f"  резервных копий: {len(self.backups)} (последняя {self.backups[-1].name})")
        return out


def _line_ending(text: str) -> str:
    index = text.find("\n")
    return "\r\n" if index > 0 and text[index - 1] == "\r" else "\n"


def brace_balance(text: str) -> int:
    """Разница ``{`` и ``}`` вне строк и комментариев ``//``."""
    depth = 0
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "/" and text[index : index + 2] == "//":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


def find_backups(gameinfo_path: Path) -> list[Path]:
    path = Path(gameinfo_path)
    return sorted(path.parent.glob(path.name + ".bhop-bak-*"))


def status(gameinfo_path: Path) -> GameInfoStatus:
    path = Path(gameinfo_path)
    if not path.is_file():
        return GameInfoStatus(path=path, exists=False)
    text = path.read_text(encoding="utf-8", errors="replace")
    delta = brace_balance(text)
    return GameInfoStatus(
        path=path,
        exists=True,
        has_addons_path=bool(ADDONS_RE.search(text)),
        braces_balanced=delta == 0,
        brace_delta=delta,
        ours=MARKER in text,
        managed_by_dmm=DMM_MARKER in text,
        backups=find_backups(path),
    )


@dataclass
class PatchOutcome:
    changed: bool
    path: Path
    backup: Path | None = None
    message: str = ""
    dry_run: bool = False


def _backup(path: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bhop-bak-{stamp}")
    backup.write_bytes(path.read_bytes())
    trace_log.detail(
        f"резервная копия gameinfo.gi: {backup.name} "
        f"({trace_log.human_bytes(backup.stat().st_size)})"
    )
    return backup


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _describe_edit(path: Path, text: str, *, what: str) -> None:
    """Одна строка о том, что стало с gameinfo.gi после правки."""
    trace_log.detail(
        f"{what}: {path} — {len(text)} символов, {text.count(chr(10)) + 1} строк, "
        f"sha256 {_sha256(text)[:16]}…"
    )


def _insertion(indent: str, ending: str) -> str:
    """Блок, который вставляется перед ``Game citadel``: маркер + строка пути."""
    return f"{indent}{MARKER}{ending}{indent}{ADDONS_LINE}{ending}"


def _removal_pattern(ending: str) -> re.Pattern[str]:
    """Шаблон для удаления нашего блока (маркер + следующая за ним строка пути).

    Отступы захватываются по факту, поэтому снимаются и правки, сделанные
    предыдущими версиями инструмента с другим форматированием.
    """
    return re.compile(
        rf"^[ \t]*{re.escape(MARKER)}[ \t]*(?:{re.escape(ending)}|\n)"
        rf"[ \t]*Game[ \t]+citadel/addons[ \t]*(?:{re.escape(ending)})?",
        re.MULTILINE,
    )


def enable_addons_path(gameinfo_path: Path, *, dry_run: bool = False) -> PatchOutcome:
    """Добавить ``Game citadel/addons`` перед ``Game citadel`` (идемпотентно).

    Порядок в ``SearchPaths`` — это приоритет: что объявлено раньше, то
    «монтируется первым» и перекрывает следующие пути (см. комментарий Valve про
    ``Game_UILanguage``). Поэтому строка добавляется **перед** ``Game citadel``:
    иначе файлы мода проигрывали бы ванильным, и патч HUD просто не подхватился.
    """
    path = Path(gameinfo_path)
    if not path.is_file():
        raise GameInfoError(f"gameinfo.gi не найден: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    ending = _line_ending(text)
    trace_log.detail(f"правлю {path} (окончания строк: {'CRLF' if ending == chr(13) + chr(10) else 'LF'})")

    if ADDONS_RE.search(text):
        trace_log.decision("строка citadel/addons", "уже есть", "правка не нужна — файл не меняется")
        return PatchOutcome(
            changed=False, path=path, dry_run=dry_run,
            message="строка citadel/addons уже есть — правка не нужна",
        )

    before_delta = brace_balance(text)
    if before_delta != 0:
        raise GameInfoError(
            f"gameinfo.gi уже повреждён (разница скобок {before_delta:+d}); "
            "почини его через «Проверить целостность файлов» в Steam"
        )

    anchor = CITADEL_GAME_RE.search(text)
    if anchor is None:
        raise GameInfoError("в gameinfo.gi не найден блок SearchPaths с 'Game citadel'")

    line_start = text.rfind("\n", 0, anchor.start()) + 1
    line_prefix = text[line_start : anchor.start()]
    indent = line_prefix[: len(line_prefix) - len(line_prefix.lstrip(" \t"))]
    insertion = _insertion(indent, ending)
    patched = text[:line_start] + insertion + text[line_start:]
    trace_log.detail(
        f"вставляю «{ADDONS_LINE.strip()}» перед строкой "
        f"{text.count(chr(10), 0, anchor.start()) + 1} (отступ «{indent}»)"
    )

    after_delta = brace_balance(patched)
    if after_delta != 0:
        raise GameInfoError(f"после правки нарушился баланс скобок ({after_delta:+d}) — запись отменена")

    if dry_run:
        trace_log.detail("пробный запуск: правка не записана")
        return PatchOutcome(changed=True, path=path, dry_run=True, message="пробный запуск: правка не записана")

    backup = _backup(path)
    path.write_text(patched, encoding="utf-8", newline="")
    _describe_edit(path, patched, what="gameinfo.gi обновлён")
    return PatchOutcome(
        changed=True, path=path, backup=backup,
        message=f"добавлена строка citadel/addons; копия оригинала: {backup.name}",
    )


def disable_addons_path(gameinfo_path: Path, *, dry_run: bool = False, from_backup: bool = False) -> PatchOutcome:
    """Убрать наши правки (или восстановить файл из последней копии)."""
    path = Path(gameinfo_path)
    if not path.is_file():
        raise GameInfoError(f"gameinfo.gi не найден: {path}")

    if from_backup:
        backups = find_backups(path)
        if not backups:
            raise GameInfoError("резервных копий нет — восстанавливать нечего")
        backup = backups[-1]
        trace_log.detail(f"восстанавливаю gameinfo.gi из копии {backup.name}")
        if dry_run:
            return PatchOutcome(changed=True, path=path, backup=backup, dry_run=True,
                                message=f"пробный запуск: восстановил бы {backup.name}")
        path.write_bytes(backup.read_bytes())
        trace_log.detail(f"gameinfo.gi восстановлен: {path}")
        return PatchOutcome(changed=True, path=path, backup=backup,
                            message=f"gameinfo.gi восстановлен из {backup.name}")

    text = path.read_text(encoding="utf-8", errors="replace")
    ending = _line_ending(text)

    pattern = _removal_pattern(ending)
    if not pattern.search(text):
        trace_log.decision("правки в gameinfo.gi", "нет", "строки citadel/addons и маркера нет — файл не меняется")
        return PatchOutcome(changed=False, path=path, dry_run=dry_run, message="наших правок в файле нет")
    patched = pattern.sub("", text, count=1)
    if brace_balance(patched) != 0:
        raise GameInfoError("после отката нарушился баланс скобок — запись отменена")
    if dry_run:
        return PatchOutcome(changed=True, path=path, dry_run=True, message="пробный запуск: откат не записан")

    backup = _backup(path)
    path.write_text(patched, encoding="utf-8", newline="")
    _describe_edit(path, patched, what="gameinfo.gi без правок мода")
    return PatchOutcome(changed=True, path=path, backup=backup,
                        message="строка citadel/addons удалена (это затронет и другие моды)")
