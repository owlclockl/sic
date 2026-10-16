"""vpk-modinfo v1 — встроенные метаданные внутрь VPK.

Реализация нейтральной спецификации ``vpk-modinfo`` (схема v1, версия 1):
в корень пака добавляются две записи —

* ``addoninfo.txt`` — человекочитаемый KeyValues1-блок (его показывают
  VPKEdit / GCFScape / Source 2 Viewer);
* ``modinfo.json`` — машинная запись целиком.

Смысл: VPK, покинувший папку сборки, остаётся самоидентифицируемым без сети —
видно название, автора, сборку и «канонический» хеш исходного файла.
Метаданные пишутся вторым проходом паковки, поэтому канонический
``originalSha256`` — это хеш файла ДО первой прошивки (см. «The canonical
identity model» в спецификации).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from vpk import (INLINE_ARCHIVE_INDEX, VpkArchive, VpkError, build_vpk, crc32_of,
                 normalize_virtual_path, sha256_of)

FORMAT = "vpk-modinfo"
SCHEMA_VERSION = 1
ADDONINFO_ENTRY = "addoninfo.txt"
MODINFO_ENTRY = "modinfo.json"

#: Игра, для которой собирается пак (значения зафиксированы спецификацией).
GAME = {"name": "Deadlock", "steamAppId": 1422450, "gameBananaGameId": 20948}

#: Порядок ключей в ``addoninfo.txt`` (спецификация требует именно такой).
_ADDONINFO_KEY_ORDER = (
    "addonversion",
    "addontitle",
    "addonauthor",
    "buildDate",
    "originalSha256",
    "originalSize",
    "originalCrc32",
    "modinfoVersion",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class ModInfoError(Exception):
    """Ошибка прошивки метаданных (спецификация запрещает писать в этих случаях)."""


@dataclass(frozen=True)
class Identity:
    """Канонический (до первой прошивки) идентификатор файла."""

    sha256: str
    size: int
    crc32: str

    @classmethod
    def of_bytes(cls, data: bytes) -> "Identity":
        return cls(sha256=sha256_of(data), size=len(data), crc32=f"{crc32_of(data):08x}")

    def to_json(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size": self.size, "crc32": self.crc32}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Identity | None":
        sha256 = str(raw.get("sha256", "")).lower()
        if not _SHA256_RE.match(sha256):
            return None
        size = raw.get("size")
        crc32 = str(raw.get("crc32", "")).lower()
        return cls(
            sha256=sha256,
            size=int(size) if isinstance(size, int) else 0,
            crc32=crc32 if re.match(r"^[0-9a-f]{8}$", crc32) else "",
        )


@dataclass(frozen=True)
class Embed:
    """Прочитанные из VPK метаданные."""

    identity: Identity
    title: str
    author: str
    first_imprinted_at: str
    written_at: str
    written_by: dict[str, str]
    kind: str


def _iso_now(moment: _dt.datetime | None = None) -> str:
    moment = moment or _dt.datetime.now(_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _kv1_value(value: str) -> str:
    """Значение KV1: одна строка, без управляющих символов, экранирование."""

    cleaned = "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in value)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    cleaned = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{cleaned}"'


def render_addoninfo(
    *,
    title: str,
    author: str,
    version: str,
    build_date: str,
    identity: Identity,
) -> str:
    """Собрать ``addoninfo.txt`` строго по правилам спецификации."""
    values = {
        "addonversion": version or "1.0",
        "addontitle": title,
        "addonauthor": author,
        "buildDate": build_date,
        "originalSha256": identity.sha256,
        "originalSize": str(identity.size),
        "originalCrc32": identity.crc32,
        "modinfoVersion": str(SCHEMA_VERSION),
    }
    lines = ['"AddonInfo"', "{"]
    for key in _ADDONINFO_KEY_ORDER:
        value = values.get(key, "")
        if value == "":
            continue  # спецификация: отсутствующий ключ просто опускается
        lines.append(f"    {key} {_kv1_value(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_modinfo(
    *,
    title: str,
    author: str,
    version: str,
    description: str,
    tool: str,
    tool_version: str,
    identity: Identity,
    written_at: str,
    first_imprinted_at: str,
) -> str:
    """Собрать ``modinfo.json`` (2 пробела, фиксированный порядок ключей)."""
    record: dict[str, Any] = {
        "format": FORMAT,
        "schemaVersion": SCHEMA_VERSION,
        "kind": "mod",
        "writtenBy": {"tool": tool, "version": tool_version},
        "writtenAt": written_at,
        "firstImprintedAt": first_imprinted_at,
        "game": GAME,
        "identity": identity.to_json(),
        "title": title,
    }
    if author:
        record["author"] = author
    if description:
        record["description"] = description
    # Своих полей не добавляем: версия мода живёт в addonversion (KV1),
    # а незнакомые ключи спецификация разрешает только как чужие расширения.
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"


def read_embed(vpk_bytes: bytes) -> Embed | None:
    """Прочитать встроенные метаданные. Отсутствие/поломка — это ``None``."""
    try:
        archive = VpkArchive(Path("<memory>"), vpk_bytes)
    except VpkError:
        return None

    identity: Identity | None = None
    addoninfo = archive.find(ADDONINFO_ENTRY)
    if addoninfo is not None:
        try:
            text = archive.read_entry(addoninfo).decode("utf-8", "replace")
        except (VpkError, UnicodeDecodeError):
            text = ""
        match = re.search(r'originalSha256\s+"?([0-9a-fA-F]{64})"?', text)
        if match:
            size_match = re.search(r'originalSize\s+"?(\d+)"?', text)
            crc_match = re.search(r'originalCrc32\s+"?([0-9a-fA-F]{8})"?', text)
            identity = Identity(
                sha256=match.group(1).lower(),
                size=int(size_match.group(1)) if size_match else 0,
                crc32=crc_match.group(1).lower() if crc_match else "",
            )

    record: dict[str, Any] = {}
    entry = archive.find(MODINFO_ENTRY)
    if entry is not None:
        try:
            parsed = json.loads(archive.read_entry(entry).decode("utf-8"))
            if isinstance(parsed, dict) and parsed.get("format") == FORMAT \
                    and parsed.get("schemaVersion") == SCHEMA_VERSION:
                record = parsed
        except (VpkError, UnicodeDecodeError, json.JSONDecodeError):
            record = {}

    if identity is None:
        raw_identity = record.get("identity")
        if isinstance(raw_identity, Mapping):
            identity = Identity.from_json(raw_identity)
    if identity is None:
        return None

    written_by = record.get("writtenBy")
    return Embed(
        identity=identity,
        title=str(record.get("title", "")),
        author=str(record.get("author", "")),
        first_imprinted_at=str(record.get("firstImprintedAt", "")),
        written_at=str(record.get("writtenAt", "")),
        written_by=written_by if isinstance(written_by, dict) else {},
        kind=str(record.get("kind", "mod")),
    )


def imprint(
    vpk_bytes: bytes,
    *,
    title: str,
    author: str = "",
    version: str = "",
    description: str = "",
    tool: str = "sic-bhop-toolchain",
    tool_version: str = "1.0.0",
    moment: _dt.datetime | None = None,
) -> tuple[bytes, Identity]:
    """Вшить метаданные в готовый VPK и вернуть ``(новые байты, канонический identity)``.

    Соблюдаются требования спецификации:

    * обе записи пишутся в одном перепаковочном проходе;
    * паритет проверяется до возврата результата (ничего лишнего не потеряно);
    * multi-chunk паки (``archive_index`` != ``0x7FFF``) отвергаются — их перепаковка
      без sibling-архивов осиротила бы данные;
    * чужой ``addoninfo.txt`` без распознаваемого identity не затирается;
    * при повторной прошивке identity и ``firstImprintedAt`` переносятся как есть.
    """
    source = VpkArchive(Path("<memory>"), vpk_bytes)
    for entry in source.entries:
        if entry.archive_index != INLINE_ARCHIVE_INDEX:
            raise ModInfoError(
                f"{entry.path}: пак многокомпонентный (archive_index={entry.archive_index}); "
                "прошивка такого пака не поддерживается"
            )

    existing = read_embed(vpk_bytes)
    if existing is None and source.contains(ADDONINFO_ENTRY):
        current = source.extract(ADDONINFO_ENTRY).decode("utf-8", "replace")
        if "originalSha256" not in current:
            raise ModInfoError(
                "в паке уже есть addoninfo.txt без распознаваемого vpk-modinfo identity — "
                "не перезаписываю чужой блок"
            )

    identity = existing.identity if existing else Identity.of_bytes(vpk_bytes)
    first_imprinted_at = existing.first_imprinted_at if existing else _iso_now(moment)
    written_at = _iso_now(moment)

    payloads: dict[str, bytes] = {}
    for entry in source.entries:
        if entry.path in (ADDONINFO_ENTRY, MODINFO_ENTRY):
            continue  # записи метаданных перезаписываются
        payloads[entry.path] = source.read_entry(entry)

    payloads[ADDONINFO_ENTRY] = render_addoninfo(
        title=title, author=author, version=version, build_date=written_at, identity=identity
    ).encode("utf-8")
    payloads[MODINFO_ENTRY] = render_modinfo(
        title=title,
        author=author,
        version=version,
        description=description,
        tool=tool,
        tool_version=tool_version,
        identity=identity,
        written_at=written_at,
        first_imprinted_at=first_imprinted_at,
    ).encode("utf-8")

    result = build_vpk(payloads)

    # Паритет: все исходные записи на месте и не изменились, добавлены ровно две.
    check = VpkArchive(Path("<memory>"), result)
    for entry in source.entries:
        if entry.path in (ADDONINFO_ENTRY, MODINFO_ENTRY):
            continue
        after = check.find(entry.path)
        if after is None:
            raise ModInfoError(f"паритет нарушен: запись {entry.path} потеряна")
        if after.length != entry.length:
            raise ModInfoError(f"паритет нарушен: {entry.path} изменил размер")
        if check.read_entry(after) != source.read_entry(entry):
            raise ModInfoError(f"паритет нарушен: {entry.path} изменилось содержимое")
    # Состав после прошивки = исходные записи + обе записи метаданных
    # (при повторной прошивке они уже были в исходнике — множество то же).
    expected_paths = {normalize_virtual_path(entry.path) for entry in source.entries}
    expected_paths |= {
        normalize_virtual_path(ADDONINFO_ENTRY),
        normalize_virtual_path(MODINFO_ENTRY),
    }
    if set(check.list_entries()) != expected_paths:
        raise ModInfoError("паритет нарушен: состав записей после прошивки неожиданный")

    return result, identity
