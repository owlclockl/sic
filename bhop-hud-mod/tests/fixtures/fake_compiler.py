#!/usr/bin/env python3
"""Заглушка ``resourcecompiler.exe`` для тестов пайплайна.

Настоящий компилятор существует только на Windows внутри CSDK, поэтому в
тестах его роль играет этот скрипт: он кладёт в ``--output-root`` файл с
расширением из таблицы компиляции и содержимым, которое заведомо отличается от
исходника (``FAKE-COMPILED``). Так проверяется оркестрация шага compile:
раскладка входов/выходов, имена ``*_c`` и то, что в VPK попадает именно
скомпилированный ресурс, а не копия источника.
"""

from __future__ import annotations

import argparse
from pathlib import Path

COMPILED_EXTENSIONS = {
    ".xml": ".vxml_c",
    ".vxml": ".vxml_c",
    ".css": ".vcss_c",
    ".vcss": ".vcss_c",
    ".js": ".vjs_c",
    ".vjs": ".vjs_c",
    ".svg": ".vsvg_c",
    ".png": ".vtex_c",
    ".wav": ".vsnd_c",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--content-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("-game", dest="game", default=None)
    args, _unknown = parser.parse_known_args()

    source = Path(args.source)
    content_root = Path(args.content_root)
    output_root = Path(args.output_root)
    relative = source.relative_to(content_root)
    extension = COMPILED_EXTENSIONS.get(relative.suffix.lower())
    if extension is None:
        print(f"fake resourcecompiler: неизвестное расширение {relative.suffix}")
        return 1

    target = output_root / relative.with_suffix(extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = b"FAKE-COMPILED\n" + source.read_bytes()
    target.write_bytes(payload)
    print(f"fake resourcecompiler: {relative} -> {target.name} ({len(payload)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
