"""VPK v2 (Valve Pak): запись, чтение, проверка.

Формат намеренно совпадает с тем, что пишет и читает Deadlock Mod Manager
(``packages/vpkmanager/src/pack.rs`` — writer, ``packages/vpk-parser`` — reader):

* заголовок v2 — 28 байт (signature, version, tree_size, data_size и три
  нулевых размера секций MD5/подписи);
* данные всех файлов лежат inline, в самой ``_dir.vpk`` (``archive_index`` =
  ``0x7FFF``), поэтому мод — это один самодостаточный файл;
* пути внутри дерева — нижним регистром, разделитель ``/``;
* после записи каждого файла — терминатор ``0xFFFF`` (как у Valve и у DMM).

Читатель умеет и «настоящие» игровые паки: если запись ссылается на
companion-архив (``pak01_000.vpk``), байты берутся из него — это нужно, чтобы
достать ванильный ``panorama/layout/hud.xml`` прямо из паков установленной игры.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, Mapping, Sequence

SIGNATURE = 0x55AA1234
VERSION = 2
HEADER_V1_SIZE = 12
HEADER_V2_SIZE = 28
INLINE_ARCHIVE_INDEX = 0x7FFF
ENTRY_TERMINATOR = 0xFFFF


class VpkError(Exception):
    """Ошибка формата или структуры VPK."""


@dataclass(frozen=True)
class Entry:
    """Одна запись дерева VPK."""

    path: str  # полный виртуальный путь нижним регистром, через '/'
    crc32: int
    preload_bytes: int
    archive_index: int
    offset: int
    length: int

    @property
    def is_inline(self) -> bool:
        return self.archive_index == INLINE_ARCHIVE_INDEX


def crc32_of(data: bytes) -> int:
    """CRC-32/ISO-HDLC — тот же полином, что у DMM (CRC_32_ISO_HDLC)."""
    return zlib.crc32(data) & 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# writer
# --------------------------------------------------------------------------- #


def normalize_virtual_path(path: str) -> str:
    """Виртуальный путь -> канонический вид (нижний регистр, прямые слэши)."""
    return path.replace("\\", "/").strip("/").lower()


def _split_entry_path(virtual_path: str) -> tuple[str, str, str]:
    """``panorama/styles/foo.vcss_c`` -> (``vcss_c``, ``panorama/styles``, ``foo``).

    Повторяет ``Path::extension``/``file_stem`` из DMM: расширение — то, что
    после последней точки, каталог — всё до имени файла.
    """
    path = Path(virtual_path)
    name = path.name
    if "." not in name or name.startswith("."):
        raise VpkError(f"у файла нет расширения: {virtual_path!r}")
    stem, _, extension = name.rpartition(".")
    parent = path.parent.as_posix()
    directory = " " if parent in ("", ".") else parent
    if not stem:
        raise VpkError(f"у файла нет имени: {virtual_path!r}")
    return extension, directory, stem


def build_vpk(entries: Mapping[str, bytes]) -> bytes:
    """Собрать VPK v2 с inline-данными.

    ``entries`` — отображение «виртуальный путь -> байты». Порядок записи
    детерминирован (сортировка по пути), поэтому один и тот же вход даёт
    побайтово один и тот же файл.
    """
    if not entries:
        raise VpkError("нечего паковать: список файлов пуст")

    data = bytearray()
    # extension -> directory -> [(name, crc, offset, length), ...]
    tree: Dict[str, Dict[str, list[tuple[str, int, int, int]]]] = {}

    for virtual_path in sorted(entries, key=normalize_virtual_path):
        payload = entries[virtual_path]
        if not isinstance(payload, (bytes, bytearray)):
            raise VpkError(f"{virtual_path}: ожидались байты")
        canonical = normalize_virtual_path(virtual_path)
        extension, directory, stem = _split_entry_path(canonical)
        offset = len(data)
        if len(data) + len(payload) > 0xFFFFFFFF:
            raise VpkError("данные VPK больше 4 ГиБ")
        data.extend(payload)
        tree.setdefault(extension, {}).setdefault(directory, []).append(
            (stem, crc32_of(bytes(payload)), offset, len(payload))
        )

    directory_tree = bytearray()
    for extension in sorted(tree):
        directory_tree += extension.encode("utf-8") + b"\0"
        for directory in sorted(tree[extension]):
            directory_tree += directory.encode("utf-8") + b"\0"
            for stem, crc, offset, length in sorted(tree[extension][directory]):
                directory_tree += stem.encode("utf-8") + b"\0"
                directory_tree += struct.pack("<IHHIIH", crc, 0, INLINE_ARCHIVE_INDEX, offset, length, ENTRY_TERMINATOR)
            directory_tree += b"\0"
        directory_tree += b"\0"
    directory_tree += b"\0"

    header = struct.pack(
        "<7I",
        SIGNATURE,
        VERSION,
        len(directory_tree),
        len(data),
        0,  # archive md5 section
        0,  # other md5 section
        0,  # signature section
    )
    return bytes(header + directory_tree + bytes(data))


def build_vpk_from_dir(root: Path, files: Iterable[Path] | None = None) -> bytes:
    """Упаковать каталог: ключи VPK — пути относительно ``root``."""
    root = Path(root)
    if not root.is_dir():
        raise VpkError(f"нет такого каталога: {root}")
    if files is None:
        files = sorted(path for path in root.rglob("*") if path.is_file())
    entries: Dict[str, bytes] = {}
    for path in files:
        relative = Path(path).relative_to(root).as_posix()
        entries[relative] = Path(path).read_bytes()
    return build_vpk(entries)


# --------------------------------------------------------------------------- #
# reader
# --------------------------------------------------------------------------- #


class VpkArchive:
    """Читатель VPK (v1 и v2, inline и companion-архивы)."""

    def __init__(self, path: Path, buffer: bytes):
        self.path = Path(path)
        self.buffer = buffer
        self.header_size, self.version, self.tree_size, self.data_size = _read_header(buffer)
        self.entries: list[Entry] = _read_tree(buffer, self.header_size, self.tree_size)
        self._index = {entry.path: entry for entry in self.entries}

    # -- интроспекция ------------------------------------------------------ #

    @property
    def data_section_start(self) -> int:
        return self.header_size + self.tree_size

    def list_entries(self) -> list[str]:
        return [entry.path for entry in self.entries]

    def contains(self, virtual_path: str) -> bool:
        return normalize_virtual_path(virtual_path) in self._index

    def find(self, virtual_path: str) -> Entry | None:
        return self._index.get(normalize_virtual_path(virtual_path))

    # -- чтение ------------------------------------------------------------ #

    def companion_path(self, archive_index: int) -> Path:
        stem = self.path.stem
        base = stem[: -len("_dir")] if stem.endswith("_dir") else stem
        return self.path.parent / f"{base}_{archive_index:03d}.vpk"

    def read_entry(self, entry: Entry) -> bytes:
        if entry.length == 0:
            return b""
        if entry.is_inline:
            start = self.data_section_start + entry.offset
            end = start + entry.length
            if end > len(self.buffer):
                raise VpkError(f"{entry.path}: inline-данные выходят за границы файла")
            return self.buffer[start:end]
        companion = self.companion_path(entry.archive_index)
        if not companion.is_file():
            raise VpkError(
                f"{entry.path}: нужен companion-архив {companion.name}, но его нет рядом с {self.path.name}"
            )
        with companion.open("rb") as handle:
            handle.seek(entry.offset)
            payload = handle.read(entry.length)
        if len(payload) != entry.length:
            raise VpkError(f"{entry.path}: в {companion.name} не хватило данных")
        return payload

    def extract(self, virtual_path: str) -> bytes:
        entry = self.find(virtual_path)
        if entry is None:
            raise VpkError(f"{virtual_path}: нет такой записи в {self.path.name}")
        return self.read_entry(entry)

    def extract_all(self, destination: Path, only: Sequence[str] | None = None) -> list[str]:
        """Распаковать всё (или указанные записи) в каталог. Пути санитизируются."""
        destination = Path(destination)
        written: list[str] = []
        wanted = {normalize_virtual_path(item) for item in only} if only else None
        for entry in self.entries:
            if wanted is not None and entry.path not in wanted:
                continue
            relative = _safe_relative(entry.path)
            if relative is None:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.read_entry(entry))
            written.append(entry.path)
        return written


def _read_header(buffer: bytes) -> tuple[int, int, int, int]:
    if len(buffer) < HEADER_V1_SIZE:
        raise VpkError("файл меньше заголовка VPK")
    signature, version, tree_size = struct.unpack_from("<3I", buffer, 0)
    if signature != SIGNATURE:
        raise VpkError(f"не VPK: сигнатура 0x{signature:08X}, ожидалась 0x{SIGNATURE:08X}")
    if version > VERSION:
        raise VpkError(f"версия VPK {version} не поддерживается (понимаем до {VERSION})")
    header_size = HEADER_V1_SIZE
    data_size = 0
    if version >= 2:
        if len(buffer) < HEADER_V2_SIZE:
            raise VpkError("обрезанный заголовок VPK v2")
        _, data_size, _, _, _ = struct.unpack_from("<5I", buffer, 4)
        header_size = HEADER_V2_SIZE
    return header_size, version, tree_size, data_size


def _read_tree(buffer: bytes, header_size: int, tree_size: int) -> list[Entry]:
    tree_end = header_size + tree_size
    if tree_end > len(buffer):
        raise VpkError("дерево VPK выходит за границы файла")
    cursor = header_size
    entries: list[Entry] = []

    def read_cstring() -> str:
        nonlocal cursor
        terminator = buffer.find(b"\0", cursor, tree_end)
        if terminator < 0:
            raise VpkError("незавершённая строка в дереве VPK")
        raw = buffer[cursor:terminator]
        cursor = terminator + 1
        return raw.decode("utf-8", "replace")

    while cursor < tree_end:
        extension = read_cstring()
        if extension == "":
            break
        while cursor < tree_end:
            directory = read_cstring()
            if directory == "":
                break
            while cursor < tree_end:
                stem = read_cstring()
                if stem == "":
                    break
                if cursor + 18 > len(buffer):
                    raise VpkError("обрывается запись дерева VPK")
                crc, preload, archive_index, offset, length, _terminator = struct.unpack_from(
                    "<IHHIIH", buffer, cursor
                )
                cursor += 18
                full_path = "/".join(part for part in ("" if directory == " " else directory, f"{stem}.{extension}") if part)
                entries.append(
                    Entry(
                        path=normalize_virtual_path(full_path),
                        crc32=crc,
                        preload_bytes=preload,
                        archive_index=archive_index,
                        offset=offset,
                        length=length,
                    )
                )
                if preload:
                    cursor += preload
                    if cursor > len(buffer):
                        raise VpkError("preload-данные выходят за границы файла")
    return entries


def _safe_relative(virtual_path: str) -> Path | None:
    parts = [part for part in virtual_path.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return Path(*parts)


def open_vpk(path: Path) -> VpkArchive:
    path = Path(path)
    return VpkArchive(path, path.read_bytes())


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# проверка и извлечение из паков игры
# --------------------------------------------------------------------------- #


def verify_vpk(vpk_bytes: bytes, expected: Mapping[str, bytes]) -> list[str]:
    """Сверить собранный VPK с ожидаемым набором файлов.

    Возвращает список проблем (пустой — всё сошлось): состав записей, размеры,
    CRC32 из дерева и побайтовое содержимое.
    """
    problems: list[str] = []
    try:
        archive = VpkArchive(Path("<memory>"), vpk_bytes)
    except VpkError as error:
        return [f"собранный VPK не читается: {error}"]

    expected_paths = {normalize_virtual_path(name) for name in expected}
    actual_paths = set(archive.list_entries())
    for missing in sorted(expected_paths - actual_paths):
        problems.append(f"в VPK нет записи {missing}")
    for extra in sorted(actual_paths - expected_paths):
        problems.append(f"в VPK лишняя запись {extra}")

    for name, payload in expected.items():
        canonical = normalize_virtual_path(name)
        entry = archive.find(canonical)
        if entry is None:
            continue
        if entry.length != len(payload):
            problems.append(f"{canonical}: длина {entry.length} != {len(payload)}")
            continue
        if not entry.is_inline:
            problems.append(f"{canonical}: данные не inline (archive_index={entry.archive_index})")
        if entry.crc32 != crc32_of(payload):
            problems.append(f"{canonical}: CRC32 в дереве не совпадает с содержимым")
        try:
            actual = archive.read_entry(entry)
        except VpkError as error:
            problems.append(f"{canonical}: {error}")
            continue
        if actual != payload:
            problems.append(f"{canonical}: содержимое отличается от исходного файла")
    return problems


def iter_pak_family(dir_vpk: Path) -> Iterator[Path]:
    """Все файлы пака: ``pakNN_dir.vpk`` и его companion-архивы."""
    dir_vpk = Path(dir_vpk)
    stem = dir_vpk.stem
    base = stem[: -len("_dir")] if stem.endswith("_dir") else stem
    yield dir_vpk
    for index in range(1000):
        companion = dir_vpk.parent / f"{base}_{index:03d}.vpk"
        if not companion.is_file():
            break
        yield companion


def find_pak_candidates(game_citadel_dir: Path) -> list[Path]:
    """Все ``pakNN_dir.vpk`` каталога игры по возрастанию номера (``pak01`` первым)."""
    game_citadel_dir = Path(game_citadel_dir)
    return sorted(Path(game_citadel_dir).glob("pak*_dir.vpk"))


def extract_from_game(game_citadel_dir: Path, virtual_path: str) -> bytes | None:
    """Достать файл из паков игры.

    Берём первый пак по возрастанию номера: нужен *ванильный* файл, а
    ``pak01`` — базовые ассеты игры (моды живут в ``citadel/addons`` и сюда
    обычно не попадают).
    """
    wanted = normalize_virtual_path(virtual_path)
    for pak in find_pak_candidates(game_citadel_dir):
        try:
            archive = open_vpk(pak)
        except (VpkError, OSError):
            continue
        if archive.contains(wanted):
            return archive.extract(wanted)
    return None
