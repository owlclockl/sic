"""Сборка пакета для Deadlock Mod Manager: «Deadlock Mod Interchange».

DMM умеет принимать сторонние моды двумя путями, и здесь готовятся оба:

1. **Импорт пакета** — «Import → Open interchange bundle» → выбрать
   ``mod-interchange.json``. Формат описан в RFC DMM
   (``rfcs/001-mod-interchange/proposal.md``): каталог с манифестом и
   ``files/<папка>/<файл>.vpk``.
2. **Перетаскивание архива** — zip с ``.vpk`` внутри бросается в «Add Mods»;
   DMM распаковывает его и находит VPK рекурсивно.

Ключи:

* ``key`` = ``local:<sha256 основного VPK>`` — по RFC локальные моды
  идентифицируются хешем, поэтому повторный импорт того же файла распознаётся
  как дубликат;
* ``origin.localId`` = ``local-<uuid>``, где uuid выводится из ключа так же,
  как это делает сам DMM (``uuid_from_seed`` в его импортере), — идентификатор
  детерминированный, но валидный для ``SubmissionRef::parse_slug``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trace_log  # noqa: E402

from vpk import sha256_of_file

FORMAT_ID = "deadlock-mod-interchange"
FORMAT_VERSION = 1
MANIFEST_FILENAME = "mod-interchange.json"
MANAGER_ID = "sic-bhop-toolchain"

_UUID_RE = re.compile(r"^local-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOCAL_KEY_RE = re.compile(r"^local:[0-9a-f]{64}$")


class BundleError(Exception):
    """Пакет не соответствует формату."""


@dataclass(frozen=True)
class BundleFile:
    """Файл мода внутри пакета."""

    name: str  # имя, которое увидит DMM (по спецификации — простое, *.vpk)
    source: Path

    @property
    def is_primary(self) -> bool:
        return True


def local_key(primary_vpk_sha256: str) -> str:
    return f"local:{primary_vpk_sha256.lower()}"


def local_id(key: str) -> str:
    """``local-<uuid>`` — ровно как в импортере DMM (sha256(seed) → 8-4-4-4-12)."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"local-{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def slug(text: str, max_len: int = 48) -> str:
    """Имя папки внутри пакета: как ``slugify`` в DMM."""
    out: list[str] = []
    last_dash = True
    for char in text:
        if char.isalnum() and char.isascii():
            out.append(char.lower())
            last_dash = False
        elif not last_dash:
            out.append("_")
            last_dash = True
        if len(out) >= max_len:
            break
    return "".join(out).strip("_")


def build_document(
    *,
    files: Sequence[BundleFile],
    entry_folder: str,
    title: str,
    author: str = "",
    description: str = "",
    category: str = "",
    hero: str | None = None,
    link: str | None = None,
    enabled: bool = True,
    order: int = 0,
    manager_version: str = "1.0.0",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Собрать документ interchange (``version`` 1)."""
    if not files:
        raise BundleError("в пакет не передан ни один VPK")

    primary = files[0].source
    key = local_key(sha256_of_file(primary))
    entries = []
    for file in files:
        relative = f"files/{entry_folder}/{file.name}"
        entries.append(
            {
                "name": file.name,
                "path": relative,
                "sha256": sha256_of_file(file.source),
                "size": file.source.stat().st_size,
                "selected": True,
            }
        )

    document: dict[str, Any] = {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "source": {
            "manager": MANAGER_ID,
            "managerVersion": manager_version,
        },
        "contents": ["mods"],
        "mods": [
            {
                "key": key,
                "name": title,
                "enabled": bool(enabled),
                "order": int(order),
                "origin": {"provider": "local", "localId": local_id(key)},
                "author": author or None,
                "description": description or None,
                "category": category or None,
                "hero": hero,
                "link": link,
                "nsfw": False,
                "files": entries,
            }
        ],
        "warnings": [],
    }
    if created_at:
        document["createdAt"] = created_at
    return document


def write_bundle(bundle_dir: Path, document: Mapping[str, Any], files: Sequence[BundleFile]) -> Path:
    """Записать каталог пакета: манифест + ``files/<папка>/<vpk>``."""
    trace_log.detail(
        f"пишу пакет DMM: {len(document.get('mods', []))} мод(ов), {len(files)} файл(ов) -> {bundle_dir}"
    )
    bundle_dir = Path(bundle_dir)
    if bundle_dir.exists():
        for child in sorted(bundle_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            else:
                child.rmdir()
    bundle_dir.mkdir(parents=True, exist_ok=True)

    for mod in document.get("mods", []):
        for entry in mod.get("files", []):
            relative = str(entry["path"])
            target = bundle_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = next((f.source for f in files if f.name == entry["name"]), None)
            if source is None:
                raise BundleError(f"для записи {entry['name']} не передан исходный файл")
            target.write_bytes(source.read_bytes())

    manifest = bundle_dir / MANIFEST_FILENAME
    manifest.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    trace_log.detail(f"манифест пакета: {manifest} ({trace_log.human_bytes(manifest.stat().st_size)})")
    return manifest


def write_zip(zip_path: Path, bundle_dir: Path, notes: str = "") -> Path:
    """Zip, который можно и перетащить в DMM, и распаковать как пакет."""
    bundle_dir = Path(bundle_dir)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_dir).as_posix())
        if notes:
            archive.writestr("README-INSTALL.txt", notes)
    trace_log.detail(f"zip для DMM: {zip_path} ({trace_log.human_bytes(zip_path.stat().st_size)})")
    return zip_path


def _is_safe_relative(value: str) -> bool:
    if value.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", value):
        return False
    parts = [part for part in value.split("/") if part not in ("", ".")]
    return bool(parts) and not any(part == ".." for part in parts)


def verify_bundle(bundle_dir: Path) -> list[str]:
    """Проверить пакет так, как это сделает импортер DMM. Пустой список — ок."""
    bundle_dir = Path(bundle_dir)
    problems: list[str] = []
    manifest = bundle_dir / MANIFEST_FILENAME
    if not manifest.is_file():
        return [f"нет манифеста {MANIFEST_FILENAME}"]
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"манифест не парсится как JSON: {error}"]

    if document.get("format") != FORMAT_ID:
        problems.append(f"format = {document.get('format')!r}, ожидалось {FORMAT_ID!r}")
    if document.get("version") != FORMAT_VERSION:
        problems.append(f"version = {document.get('version')!r}, ожидалось {FORMAT_VERSION}")

    mods = document.get("mods") or []
    if not mods:
        problems.append("в документе нет ни одного мода")

    seen_keys: set[str] = set()
    for index, mod in enumerate(mods):
        label = f"mods[{index}]"
        key = str(mod.get("key", ""))
        if key in seen_keys:
            problems.append(f"{label}: ключ {key!r} повторяется")
        seen_keys.add(key)
        if not _LOCAL_KEY_RE.match(key) and not re.match(r"^(snd-)?\d+$", key):
            problems.append(f"{label}: ключ {key!r} не похож на валидный (local:<sha256> или id GameBanana)")

        origin = mod.get("origin") or {}
        if origin.get("provider") == "local":
            candidate = str(origin.get("localId", ""))
            if candidate and not _UUID_RE.match(candidate):
                problems.append(f"{label}: localId {candidate!r} не в форме local-<uuid>")
        elif origin.get("provider") != "gamebanana":
            problems.append(f"{label}: неизвестный provider {origin.get('provider')!r}")
        if not str(mod.get("name", "")).strip():
            problems.append(f"{label}: пустое имя мода")

        files = mod.get("files") or []
        if not files:
            problems.append(f"{label}: не перечислено ни одного VPK")
        for file_index, entry in enumerate(files):
            file_label = f"{label}.files[{file_index}]"
            name = str(entry.get("name", ""))
            relative = str(entry.get("path", ""))
            if not name.lower().endswith(".vpk") or "/" in name or "\\" in name:
                problems.append(f"{file_label}: name {name!r} не является простым именем .vpk")
            if not _is_safe_relative(relative):
                problems.append(f"{file_label}: path {relative!r} выходит за пределы пакета")
                continue
            target = bundle_dir / relative
            if not target.is_file():
                problems.append(f"{file_label}: файла {relative} нет в пакете")
                continue
            payload = target.read_bytes()
            if entry.get("size") is not None and int(entry["size"]) != len(payload):
                problems.append(f"{file_label}: size не совпадает с файлом")
            recorded = str(entry.get("sha256", "")).lower()
            if recorded and recorded != hashlib.sha256(payload).hexdigest():
                problems.append(f"{file_label}: sha256 не совпадает с файлом")
    return problems


def bundle_summary(bundle_dir: Path) -> dict[str, Any]:
    """Краткая сводка по пакету (для отчёта и ``--json``)."""
    manifest = Path(bundle_dir) / MANIFEST_FILENAME
    document = json.loads(manifest.read_text(encoding="utf-8"))
    mod = (document.get("mods") or [{}])[0]
    files = [entry["path"] for entry in mod.get("files", [])]
    total = sum((Path(bundle_dir) / entry["path"]).stat().st_size for entry in mod.get("files", []) if (Path(bundle_dir) / entry["path"]).is_file())
    return {
        "bundle": str(bundle_dir),
        "key": mod.get("key"),
        "localId": (mod.get("origin") or {}).get("localId"),
        "title": mod.get("name"),
        "files": files,
        "bytes": total,
    }
