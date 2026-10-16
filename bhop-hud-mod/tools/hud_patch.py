"""Патч ванильного ``panorama/layout/hud.xml``: вставка панели и подключение стилей.

Мод-панорама не может «добавить» панель в HUD, не переопределив ``hud.xml``:
игра читает именно этот layout. Поэтому сборщик берёт **ванильный** файл (откуда
— решает ``tools/vanilla_hud.py``), вставляет в него сниппет из
``src/bhop_trainer.vxml`` перед закрывающим ``</CitadelHud>`` и добавляет
``<include>`` стилей в блок ``<styles>`` — ровно то, что README предлагает
делать руками.

Патч идемпотентен (маркеры ``bhop-trainer:*``) и умеет откатываться.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

MARK_BEGIN = "bhop-trainer:begin"
MARK_END = "bhop-trainer:end"
MARK_STYLES = "bhop-trainer:styles"

DEFAULT_STYLE_SRC = "s2r://panorama/styles/bhop_trainer.vcss"
HUD_XML_VIRTUAL_PATH = "panorama/layout/hud.xml"


class HudPatchError(Exception):
    """Патч невозможен (нет якоря, сломанный XML и т.п.)."""


@dataclass
class PatchResult:
    text: str
    added_panel: bool = False
    added_styles: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.added_panel or self.added_styles

    @property
    def already_patched(self) -> bool:
        return not self.changed


def _line_ending(text: str) -> str:
    first_newline = text.find("\n")
    return "\r\n" if first_newline > 0 and text[first_newline - 1] == "\r" else "\n"


def load_snippet(vxml_path: Path) -> str:
    """Прочитать разметку панели из ``.vxml`` и убрать вводный комментарий."""
    text = Path(vxml_path).read_text(encoding="utf-8").replace("\r\n", "\n")
    if text.lstrip().startswith("<!--"):
        end = text.find("-->")
        if end >= 0:
            text = text[end + 3 :]
    text = text.strip("\n")
    if not text.strip():
        raise HudPatchError(f"{vxml_path}: сниппет пуст")
    if "</CitadelHud>" in text:
        raise HudPatchError(f"{vxml_path}: сниппет не должен содержать </CitadelHud>")
    return text


def _indent_of_line(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    prefix = text[start:index]
    return prefix if prefix.strip() == "" else ""


def _detect_anchor_indent(text: str, anchor: str) -> str:
    index = text.rfind(anchor)
    if index < 0:
        raise HudPatchError(f"в hud.xml не найден якорь {anchor!r}")
    return _indent_of_line(text, index)


def validate_hud_xml(text: str) -> list[str]:
    """Структурные проверки файла. Возвращает список найденных проблем."""
    problems: list[str] = []
    if "<CitadelHud" not in text:
        problems.append("в hud.xml нет элемента <CitadelHud>")
    if text.count("</CitadelHud>") < 1:
        problems.append("в hud.xml нет закрывающего </CitadelHud>")
    if "</styles>" not in text:
        problems.append("в hud.xml нет закрывающего </styles>")
    if text.count(MARK_BEGIN) != text.count(MARK_END):
        problems.append("маркеры bhop-trainer:begin/end непарные")
    if text.count(MARK_BEGIN) > 1:
        problems.append("вставка панели встречается больше одного раза")
    return problems


def _xml_is_well_formed(text: str) -> str | None:
    try:
        ElementTree.fromstring(text)
    except ElementTree.ParseError as error:  # pragma: no cover - зависит от версии питона
        return str(error)
    except ValueError as error:
        return str(error)
    return None


def patch_hud_xml(
    text: str,
    *,
    snippet: str,
    style_src: str = DEFAULT_STYLE_SRC,
    verify_xml: bool = True,
) -> PatchResult:
    """Вставить панель и подключить стили. Повторный вызов ничего не меняет."""
    ending = _line_ending(text)
    normalized = text.replace("\r\n", "\n")
    result = PatchResult(text=text)

    if MARK_BEGIN in normalized:
        result.notes.append("панель уже вставлена (маркер bhop-trainer:begin на месте)")
    else:
        indent = _detect_anchor_indent(normalized, "</CitadelHud>")
        snippet_lines = [line for line in snippet.split("\n")]
        block_lines = [f"{indent}<!-- {MARK_BEGIN} (сгенерировано сборщиком bhop_trainer) -->"]
        block_lines += [f"{indent}\t{line}" if line.strip() else "" for line in snippet_lines]
        block_lines.append(f"{indent}<!-- {MARK_END} -->")
        block = "\n".join(block_lines) + "\n"

        anchor_index = normalized.rfind("</CitadelHud>")
        line_start = normalized.rfind("\n", 0, anchor_index) + 1
        normalized = normalized[:line_start] + block + normalized[line_start:]
        result.added_panel = True

    if MARK_STYLES in normalized:
        result.notes.append("include стилей уже добавлен (маркер bhop-trainer:styles на месте)")
    else:
        anchor_index = normalized.rfind("</styles>")
        if anchor_index < 0:
            raise HudPatchError("в hud.xml нет </styles>: некуда подключать bhop_trainer.vcss")
        indent = _indent_of_line(normalized, anchor_index)
        include_block = (
            f"{indent}\t<!-- {MARK_STYLES} -->\n"
            f'{indent}\t<include src="{style_src}" />\n'
        )
        line_start = normalized.rfind("\n", 0, anchor_index) + 1
        normalized = normalized[:line_start] + include_block + normalized[line_start:]
        result.added_styles = True

    problems = validate_hud_xml(normalized)
    if problems:
        raise HudPatchError("; ".join(problems))

    if verify_xml:
        error = _xml_is_well_formed(normalized)
        if error:
            raise HudPatchError(f"после патча hud.xml не парсится как XML: {error}")

    result.text = normalized.replace("\n", ending) if ending == "\r\n" else normalized
    if not result.changed:
        # При повторном вызове текст возвращаем как есть, чтобы не переписывать
        # окончания строк в уже патченном файле.
        result.text = text
    return result


def revert_hud_xml(text: str) -> PatchResult:
    """Снять вставки мода (для отладки и для ``--unpatch``)."""
    ending = _line_ending(text)
    normalized = text.replace("\r\n", "\n")
    result = PatchResult(text=text)

    pattern = re.compile(
        rf"^[ \t]*<!--\s*{re.escape(MARK_BEGIN)}.*?-->.*?^[ \t]*<!--\s*{re.escape(MARK_END)}\s*-->\n?",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(normalized):
        normalized = pattern.sub("", normalized, count=1)
        result.added_panel = True

    style_pattern = re.compile(
        rf"^[ \t]*<!--\s*{re.escape(MARK_STYLES)}\s*-->\n[ \t]*<include src=\"[^\"]*bhop_trainer\.vcss\" />\n",
        re.MULTILINE,
    )
    if style_pattern.search(normalized):
        normalized = style_pattern.sub("", normalized, count=1)
        result.added_styles = True

    if not result.changed:
        return result
    result.text = normalized.replace("\n", ending) if ending == "\r\n" else normalized
    result.notes.append("вставки bhop_trainer удалены")
    return result


def is_patched(text: str) -> bool:
    return MARK_BEGIN in text and MARK_STYLES in text
