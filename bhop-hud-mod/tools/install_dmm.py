#!/usr/bin/env python3
"""Установщик Bhop Trainer HUD через Deadlock Mod Manager (+ служебные команды).

Основной путь — дать DMM готовый пакет и проверить результат:

    python3 tools/install_dmm.py check            # что вообще есть на машине
    python3 tools/install_dmm.py stage --open     # выложить пакет и открыть папку
    ...  в DMM: Import → Open interchange bundle → mod-interchange.json → Launch modded
    python3 tools/install_dmm.py verify           # мод на месте? gameinfo поправлен?

Дополнительно:

* ``direct`` — поставить мод мимо менеджера (свободный слот ``pakNN_dir.vpk`` +
  строка ``Game citadel/addons`` в gameinfo.gi с резервной копией);
* ``uninstall`` — убрать мод и/или откатить gameinfo.gi;
* ``cfg`` — положить конфиги тренажёра из ``bhop-trainer/`` в ``game/citadel/cfg``.

Ничего не пишется без явной команды; ``--dry-run`` показывает план и не трогает файлы.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dmm_bundle  # noqa: E402
import gameinfo  # noqa: E402
import modinfo  # noqa: E402
import paths as paths_mod  # noqa: E402
import trace_log  # noqa: E402
import vpk  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = MOD_ROOT / "dist"
BUNDLE_DIR = DIST_DIR / "dmm-bundle"
VPK_SOURCE = DIST_DIR / "bhop_trainer_dir.vpk"
CFG_SOURCE_DIR = REPO_ROOT / "bhop-trainer"
CFG_FILES = ("bhop_helper.cfg", "bhop_helper_latin.cfg", "bhop_helper_off.cfg")

#: Как узнать «наш» VPK внутри citadel/addons (скомпилированный и сырой варианты).
MARKER_ENTRIES = (
    "panorama/styles/bhop_trainer.vcss_c",
    "panorama/styles/bhop_trainer.vcss",
)
REQUIRED_COMPILED_RESOURCES = (
    "panorama/layout/hud.vxml_c",
    "panorama/styles/bhop_trainer.vcss_c",
)
MAX_SLOT = 99


@dataclass
class FoundMod:
    path: Path
    slot: int | None
    compiled: bool
    title: str = ""

    def describe(self) -> str:
        kind = "полный скомпилированный HUD" if self.compiled else "неполный/только исходники (в игре не заработает)"
        slot = f"pak{self.slot:02d}_dir.vpk" if self.slot is not None else self.path.name
        return f"{slot} — {kind} — {self.path}"


def addons_dir(game_dir: Path) -> Path:
    return Path(game_dir) / "game" / "citadel" / "addons"


def find_installed_mods(game_dir: Path) -> list[FoundMod]:
    """Найти наши VPK в citadel/addons (включая подпапки профилей DMM)."""
    root = addons_dir(game_dir)
    if not root.is_dir():
        return []
    found: list[FoundMod] = []
    for path in sorted(root.rglob("*.vpk")):
        try:
            archive = vpk.open_vpk(path)
        except (vpk.VpkError, OSError):
            continue
        with archive:
            marker = next((name for name in MARKER_ENTRIES if archive.contains(name)), None)
            has_all_compiled = all(archive.contains(name) for name in REQUIRED_COMPILED_RESOURCES)
        try:
            embed = modinfo.read_embed(path.read_bytes())
        except OSError:
            embed = None
        owned_by_builder = bool(
            embed is not None
            and embed.written_by.get("tool") == dmm_bundle.MANAGER_ID
        )
        if marker is None and not owned_by_builder:
            continue
        slot_match = re.fullmatch(r"pak(\d+)_dir\.vpk", path.name)
        found.append(
            FoundMod(
                path=path,
                slot=int(slot_match.group(1)) if slot_match else None,
                compiled=has_all_compiled,
                title=embed.title if embed is not None else "",
            )
        )
    return found


def check_installable_vpk(path: Path) -> list[str]:
    """Проверить ресурсы, без которых HUD-мод может уронить запуск игры.

    Особенно важно проверять ``hud.vxml_c``: VPK только со стилями проходит
    старую проверку маркера, но заменяет/не предоставляет layout HUD, после чего
    движок может показать «Unable to load layout file ... hud.xml».
    """
    path = Path(path)
    try:
        archive = vpk.open_vpk(path)
    except (vpk.VpkError, OSError) as error:
        return [f"VPK не читается: {error}"]

    problems: list[str] = []
    with archive:
        for resource in REQUIRED_COMPILED_RESOURCES:
            entry = archive.find(resource)
            if entry is None:
                problems.append(f"нет обязательного скомпилированного ресурса {resource}")
                continue
            try:
                data = archive.read_entry(entry, verify_crc=True)
            except vpk.VpkError as error:
                problems.append(f"{resource}: ошибка чтения/CRC: {error}")
                continue
            if not data:
                problems.append(f"обязательный ресурс пуст: {resource}")
    return problems


def used_slots(game_dir: Path) -> set[int]:
    root = addons_dir(game_dir)
    slots: set[int] = set()
    if root.is_dir():
        for path in root.glob("pak*_dir.vpk"):
            match = re.fullmatch(r"pak(\d+)_dir\.vpk", path.name)
            if match:
                slots.add(int(match.group(1)))
    return slots


def pick_slot(game_dir: Path, requested: int | None = None) -> int:
    used = used_slots(game_dir)
    if requested is not None:
        if requested in used:
            raise SystemExit(f"слот pak{requested:02d}_dir.vpk уже занят")
        if not 1 <= requested <= MAX_SLOT:
            raise SystemExit(f"номер слота должен быть в диапазоне 1..{MAX_SLOT}")
        return requested
    for number in range(1, MAX_SLOT + 1):
        if number not in used:
            return number
    raise SystemExit("в citadel/addons не осталось свободных слотов pakNN_dir.vpk (1..99)")


# --------------------------------------------------------------------------- #
# команды
# --------------------------------------------------------------------------- #


def cmd_check(args: argparse.Namespace) -> int:
    report = paths_mod.env_report(
        Path(args.game_path) if args.game_path else None,
        Path(args.csdk) if args.csdk else None,
    )
    print("== Окружение ==")
    for line in report.lines():
        print("  " + line)

    print("\n== Артефакты сборки ==")
    if VPK_SOURCE.is_file():
        data = VPK_SOURCE.read_bytes()
        archive = vpk.VpkArchive(VPK_SOURCE, data)
        print(f"  VPK: {VPK_SOURCE} ({len(data)} Б, записей {len(archive.entries)})")
        print(f"  sha256: {vpk.sha256_of(data)}")
        resource_problems = check_installable_vpk(VPK_SOURCE)
        if resource_problems:
            print("  ГОТОВНОСТЬ К УСТАНОВКЕ: НЕТ")
            for problem in resource_problems:
                print(f"    ! {problem}")
        else:
            print("  ГОТОВНОСТЬ К УСТАНОВКЕ: да (layout и стили скомпилированы)")
    else:
        print(f"  VPK не собран: {VPK_SOURCE}")
        print("  Собери: python3 tools/build_mod.py --game-path <Deadlock> --csdk <CSDK>")
    if (BUNDLE_DIR / dmm_bundle.MANIFEST_FILENAME).is_file():
        summary = dmm_bundle.bundle_summary(BUNDLE_DIR)
        print(f"  Пакет DMM: {BUNDLE_DIR}")
        print(f"    ключ {summary['key']}, localId {summary['localId']}")
    else:
        print("  Пакета DMM нет (шаг dmm в сборщике не выполнялся)")

    print("\n== Игра ==")
    if report.game_dir is None:
        print("  Deadlock не найден: передай --game-path")
    else:
        status = gameinfo.status(report.game_dir / paths_mod.GAMEINFO_RELATIVE)
        for line in status.lines():
            print("  " + line)
        found = find_installed_mods(report.game_dir)
        print(f"  наш мод в citadel/addons: {'найден' if found else 'не найден'}")
        for mod in found:
            print("    " + mod.describe())
        slots = sorted(used_slots(report.game_dir))
        print(f"  занятые слоты pak##_dir.vpk: {slots or 'нет'}")

    print("\n== Что дальше ==")
    if not VPK_SOURCE.is_file():
        print("  1) собери мод: python3 tools/build_mod.py --game-path <Deadlock> --csdk <CSDK>")
        print("  2) выложи пакет: python3 tools/install_dmm.py stage --open")
        print("  3) в DMM: Import → Open interchange bundle → mod-interchange.json → Launch modded")
        print("  4) проверь: python3 tools/install_dmm.py verify")
    else:
        print("  1) python3 tools/install_dmm.py stage --open")
        print("  2) в DMM: Import → Open interchange bundle → mod-interchange.json → Launch modded")
        print("  3) python3 tools/install_dmm.py verify")
    return 0


def _default_stage_dir() -> Path:
    home = Path.home()
    for candidate in (home / "Desktop", home / "Рабочий стол", home / "Downloads", home / "Загрузки"):
        if candidate.is_dir():
            return candidate / "bhop-trainer-dmm"
    return home / "bhop-trainer-dmm"


def cmd_stage(args: argparse.Namespace) -> int:
    if not (BUNDLE_DIR / dmm_bundle.MANIFEST_FILENAME).is_file():
        raise SystemExit(
            f"пакет не собран ({BUNDLE_DIR}): сначала выполни\n"
            "  python3 tools/build_mod.py --game-path <Deadlock> --csdk <CSDK>"
        )
    problems = dmm_bundle.verify_bundle(BUNDLE_DIR)
    if problems:
        raise SystemExit("пакет повреждён:\n  - " + "\n  - ".join(problems))

    bundled_vpks = sorted(BUNDLE_DIR.rglob("*.vpk"))
    if not bundled_vpks:
        raise SystemExit("в пакете DMM нет VPK")
    for bundled_vpk in bundled_vpks:
        problems = check_installable_vpk(bundled_vpk)
        if problems:
            raise SystemExit(
                f"отказ от публикации неполного VPK {bundled_vpk}:\n  - " + "\n  - ".join(problems)
            )

    target = Path(args.to).expanduser().resolve() if args.to else _default_stage_dir()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(BUNDLE_DIR, target)
    manifest = target / dmm_bundle.MANIFEST_FILENAME
    print(f"Пакет выложен: {target}")
    print(f"Манифест для DMM: {manifest}")
    print()
    print("В Deadlock Mod Manager:")
    print("  1) Отключи/удали старую копию Bhop Trainer HUD, если она уже есть")
    print("  2) Mods → Import → Open interchange bundle")
    print(f"  3) выбрать файл: {manifest}")
    print("  4) подтвердить импорт и оставить включённой только одну копию мода")
    print("  5) закрыть Deadlock и нажать Launch modded")
    print()
    print("Альтернатива без диалога: перетащи zip из dist/ в раздел Add Mods.")
    if args.open:
        _open_path(target)
    return 0


def _open_path(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        print(f"Открываю папку: {path}")
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Не удалось открыть проводник ({error}) — открой папку вручную: {path}")


def cmd_verify(args: argparse.Namespace) -> int:
    game_dir = paths_mod.find_game_dir(Path(args.game_path) if args.game_path else None)
    if game_dir is None:
        raise SystemExit("Deadlock не найден: передай --game-path")
    print(f"Deadlock: {game_dir}")
    status = gameinfo.status(game_dir / paths_mod.GAMEINFO_RELATIVE)
    for line in status.lines():
        print("  " + line)

    found = find_installed_mods(game_dir)
    if not found:
        print("\nМод в citadel/addons НЕ найден.")
        print("  • импорт в DMM не выполнен, мод выключен, или игра не перезапускалась;")
        print("  • проверь «My Mods» в DMM и нажми Launch modded;")
        print("  • запасной путь: python3 tools/install_dmm.py direct")
        return 1

    print("\nМод найден:")
    for mod in found:
        print("  " + mod.describe())

    problems: list[str] = []
    if len(found) > 1:
        problems.append(
            f"найдено несколько копий Bhop Trainer ({len(found)}); оставь только одну, "
            "иначе старый HUD VPK может перебить новую сборку"
        )
    for mod in found:
        resource_problems = check_installable_vpk(mod.path)
        problems.extend(f"{mod.path.name}: {problem}" for problem in resource_problems)
    if not any(mod.compiled for mod in found):
        problems.append(
            "в VPK только исходники или отсутствует часть обязательных скомпилированных ресурсов "
            "(нужны hud.vxml_c и bhop_trainer.vcss_c); пересобери с CSDK"
        )
    if not status.has_addons_path:
        problems.append("в gameinfo.gi нет «Game citadel/addons» — нужно нажать Launch modded в DMM")
    if not status.braces_balanced:
        problems.append("gameinfo.gi повреждён (скобки не сбалансированы) — восстанови через Steam")
    running = paths_mod.is_game_running()
    if running:
        problems.append("Deadlock запущен прямо сейчас: изменения подхватятся только после полного перезапуска")

    if problems:
        print("\nТребует внимания:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1
    print("\nВсё на месте: VPK в citadel/addons, путь citadel/addons включён в gameinfo.gi.")
    print("Если панели всё ещё нет в HUD — полностью закрой и запусти игру заново.")
    return 0


def cmd_direct(args: argparse.Namespace) -> int:
    game_dir = paths_mod.find_game_dir(Path(args.game_path) if args.game_path else None)
    if game_dir is None:
        raise SystemExit("Deadlock не найден: передай --game-path")
    if not VPK_SOURCE.is_file():
        raise SystemExit(
            f"нет собранного VPK ({VPK_SOURCE}): сначала собери мод\n"
            "  python3 tools/build_mod.py --game-path <Deadlock> --csdk <CSDK>"
        )
    resource_problems = check_installable_vpk(VPK_SOURCE)
    if resource_problems:
        raise SystemExit(
            "отказ от установки VPK: он не содержит полный скомпилированный HUD:\n  - "
            + "\n  - ".join(resource_problems)
            + "\nПересобери с CSDK 12; такой пакет может вызвать FATAL ERROR при запуске Deadlock."
        )

    if args.slot is not None and not 1 <= args.slot <= MAX_SLOT:
        raise SystemExit(f"номер слота должен быть в диапазоне 1..{MAX_SLOT}")

    addons_root = addons_dir(game_dir)
    installed = find_installed_mods(game_dir)
    root = addons_root.resolve()
    nested = [mod for mod in installed if mod.path.parent.resolve() != root]
    if nested:
        paths = ", ".join(str(mod.path) for mod in nested)
        raise SystemExit(
            "Bhop Trainer уже найден в профиле/подпапке DMM; прямую установку отменяю, "
            f"чтобы не смешивать две установки:\n  {paths}\n"
            "Удали или обнови старую копию в DMM, затем импортируй новый пакет через DMM."
        )

    existing_direct = [
        mod.path for mod in installed
        if mod.path.parent.resolve() == root
        and re.fullmatch(r"pak\d+_dir\.vpk", mod.path.name)
    ]
    owned_slots = {
        int(match.group(1))
        for path in existing_direct
        if (match := re.fullmatch(r"pak(\d+)_dir\.vpk", path.name))
    }
    if args.slot is not None and args.slot in owned_slots:
        slot = args.slot
    elif args.slot is not None:
        slot = pick_slot(game_dir, args.slot)
    elif owned_slots:
        # Обновляем старый прямой install на месте, а не складываем новый pakNN,
        # который мог бы проиграть оставшейся старой версии по приоритету загрузки.
        slot = min(owned_slots)
    else:
        slot = pick_slot(game_dir)

    target = addons_root / f"pak{slot:02d}_dir.vpk"
    status = gameinfo.status(game_dir / paths_mod.GAMEINFO_RELATIVE)
    source_hash = vpk.sha256_of_file(VPK_SOURCE)
    target_is_current = target.is_file() and vpk.sha256_of_file(target) == source_hash

    print(f"Deadlock: {game_dir}")
    print(f"План: {VPK_SOURCE.name} → {target}")
    if existing_direct:
        print(f"      найдено старых прямых копий Bhop Trainer: {len(existing_direct)}; обновлю на месте и сохраню backup")
    print(f"      gameinfo.gi: {'строка citadel/addons уже есть' if status.has_addons_path else 'будет добавлена строка citadel/addons'}")
    game_running = paths_mod.is_game_running()
    if game_running:
        print("      ! Deadlock сейчас запущен — изменения нельзя применять к работающей игре")
    if args.dry_run:
        print("Режим --dry-run: ничего не записано.")
        return 0
    if game_running:
        raise SystemExit("закрой Deadlock и повтори установку; файлы игры не изменены")

    addons_root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for old in existing_direct:
        if old == target and target_is_current:
            continue
        backup = old.with_name(f"{old.name}.bhop-backup-{stamp}")
        suffix = 1
        while backup.exists():
            backup = old.with_name(f"{old.name}.bhop-backup-{stamp}-{suffix}")
            suffix += 1
        shutil.copy2(old, backup)
        print(f"Резервная копия старого мода: {backup}")

    gameinfo_path = game_dir / paths_mod.GAMEINFO_RELATIVE
    temp_target = target.with_name(f".{target.name}.installing-{stamp}")
    outcome = None
    try:
        if not target_is_current:
            shutil.copy2(VPK_SOURCE, temp_target)
        outcome = gameinfo.enable_addons_path(gameinfo_path)
        if not target_is_current:
            os.replace(temp_target, target)
    except Exception:
        try:
            if temp_target.exists():
                temp_target.unlink()
        except OSError:
            pass
        if outcome is not None and outcome.changed:
            try:
                gameinfo.disable_addons_path(gameinfo_path)
            except Exception as rollback_error:
                print(f"Не удалось автоматически откатить gameinfo.gi: {rollback_error}")
        raise

    stale: list[Path] = []
    for old in existing_direct:
        if old == target:
            continue
        try:
            old.unlink()
            print(f"Удалена старая копия: {old}")
        except OSError:
            stale.append(old)

    if not target_is_current:
        print(f"Установлено: {target}")
    else:
        print(f"Уже актуально: {target}")
    print(outcome.message if outcome is not None else "gameinfo.gi не менялся")
    if stale:
        print("ВНИМАНИЕ: старые копии не удалось удалить; не запускай игру, пока не уберёшь их:")
        for path in stale:
            print(f"  {path}")
        return 1
    print("\nГотово. Полностью закрой Deadlock и запусти заново.")
    print("Откатить: python3 tools/install_dmm.py uninstall --restore-gameinfo")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    game_dir = paths_mod.find_game_dir(Path(args.game_path) if args.game_path else None)
    if game_dir is None:
        raise SystemExit("Deadlock не найден: передай --game-path")
    found = find_installed_mods(game_dir)
    if not found:
        print("Наших VPK в citadel/addons нет — удалять нечего.")
    for mod in found:
        if args.dry_run:
            print(f"[dry-run] удалил бы {mod.path}")
            continue
        mod.path.unlink()
        print(f"Удалено: {mod.path}")

    if args.restore_gameinfo:
        # --restore-gameinfo и --from-backup — синонимы, восстанавливаем из копии.
        outcome = gameinfo.disable_addons_path(
            game_dir / paths_mod.GAMEINFO_RELATIVE,
            dry_run=args.dry_run,
            from_backup=bool(args.restore_gameinfo or args.from_backup),
        )
        print(outcome.message)
    elif not args.keep_gameinfo:
        outcome = gameinfo.disable_addons_path(game_dir / paths_mod.GAMEINFO_RELATIVE, dry_run=args.dry_run)
        print(outcome.message)
        print("(другие моды используют тот же путь citadel/addons — DMM добавит его снова при запуске)")
    return 0


def cmd_cfg(args: argparse.Namespace) -> int:
    game_dir = paths_mod.find_game_dir(Path(args.game_path) if args.game_path else None)
    if game_dir is None:
        raise SystemExit("Deadlock не найден: передай --game-path")
    cfg_dir = game_dir / "game" / "citadel" / "cfg"
    if args.uninstall:
        for name in CFG_FILES:
            path = cfg_dir / name
            if path.is_file():
                if args.dry_run:
                    print(f"[dry-run] удалил бы {path}")
                else:
                    path.unlink()
                    print(f"Удалено: {path}")
        print("autoexec.cfg не трогаю: строку `exec bhop_helper.cfg` убери сам, если она больше не нужна.")
        return 0

    missing = [name for name in CFG_FILES if not (CFG_SOURCE_DIR / name).is_file()]
    sources = [CFG_SOURCE_DIR / name for name in CFG_FILES if (CFG_SOURCE_DIR / name).is_file()]
    if not sources:
        raise SystemExit(f"в {CFG_SOURCE_DIR} нет .cfg файлов тренажёра")
    print(f"Копирую конфиги тренажёра в {cfg_dir}")
    if missing:
        print(f"  (нет в репозитории, пропускаю: {', '.join(missing)})")
    if args.dry_run:
        for source in sources:
            print(f"[dry-run] {source.name} → {cfg_dir / source.name}")
        return 0
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.copy2(source, cfg_dir / source.name)
        print(f"  {source.name}")
    print("\nВключи консоль (F7) и выполни: exec bhop_helper.cfg")
    print("Автозагрузка: добавь строку `exec bhop_helper.cfg` в autoexec.cfg игры.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _common_arguments() -> argparse.ArgumentParser:
    """Общие ключи, которые можно писать и до, и после подкоманды.

    ``default=SUPPRESS`` важен: иначе подпарсер перезаписал бы значение, уже
    разобранное основным парсером (классическая ловушка argparse).
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--game-path", default=argparse.SUPPRESS,
                        help="папка Deadlock (или её game/citadel)")
    common.add_argument("--dist", default=argparse.SUPPRESS,
                        help="каталог артефактов сборки, если собирали с --out (по умолчанию dist)")
    trace_log.patch_cli(common, suppress_defaults=True)
    return common


def build_parser() -> argparse.ArgumentParser:
    parent = _common_arguments()
    parser = argparse.ArgumentParser(
        prog="install_dmm.py",
        parents=[parent],
        description="Установка Bhop Trainer HUD через Deadlock Mod Manager (и запасные режимы).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", parents=[parent], help="показать окружение, артефакты и состояние игры")
    check.add_argument("--csdk", help="путь к CSDK (для отчёта)")
    check.set_defaults(func=cmd_check)

    stage = sub.add_parser("stage", parents=[parent], help="выложить пакет DMM в удобное место и подсказать шаги")
    stage.add_argument("--to", help="каталог, куда скопировать пакет")
    stage.add_argument("--open", action="store_true", help="открыть папку в проводнике")
    stage.set_defaults(func=cmd_stage)

    verify = sub.add_parser("verify", parents=[parent], help="проверить, что мод встал и gameinfo поправлен")
    verify.set_defaults(func=cmd_verify)

    direct = sub.add_parser("direct", parents=[parent], help="запасной путь: поставить VPK мимо менеджера")
    direct.add_argument("--slot", type=int, help="номер слота pakNN_dir.vpk (по умолчанию первый свободный)")
    direct.add_argument("--dry-run", action="store_true", help="только показать план")
    direct.set_defaults(func=cmd_direct)

    uninstall = sub.add_parser("uninstall", parents=[parent], help="удалить мод и/или откатить gameinfo.gi")
    uninstall.add_argument("--restore-gameinfo", action="store_true", help="восстановить gameinfo.gi из копии")
    uninstall.add_argument("--from-backup", action="store_true", help="то же, что --restore-gameinfo")
    uninstall.add_argument("--keep-gameinfo", action="store_true", help="не трогать gameinfo.gi")
    uninstall.add_argument("--dry-run", action="store_true", help="только показать план")
    uninstall.set_defaults(func=cmd_uninstall)

    cfg = sub.add_parser("cfg", parents=[parent], help="положить конфиги тренажёра в game/citadel/cfg")
    cfg.add_argument("--uninstall", action="store_true", help="удалить их")
    cfg.add_argument("--dry-run", action="store_true", help="только показать план")
    cfg.set_defaults(func=cmd_cfg)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global DIST_DIR, BUNDLE_DIR, VPK_SOURCE
    args = build_parser().parse_args(argv)
    # У общих ключей default=SUPPRESS, поэтому атрибут может отсутствовать.
    if getattr(args, "game_path", None) is None:
        args.game_path = None
    if getattr(args, "dist", None):
        DIST_DIR = Path(args.dist).expanduser().resolve()
        BUNDLE_DIR = DIST_DIR / "dmm-bundle"
        VPK_SOURCE = DIST_DIR / "bhop_trainer_dir.vpk"
        globals().update(DIST_DIR=DIST_DIR, BUNDLE_DIR=BUNDLE_DIR, VPK_SOURCE=VPK_SOURCE)

    trace_log.reset()
    journal = trace_log.apply_cli(
        args,
        default_dir=DIST_DIR / "logs",
        prefix=f"install-{getattr(args, 'command', 'cmd')}",
        header=f"Установка мода: {getattr(args, 'command', '?')}",
    )
    journal.snapshot(extra={
        "команда": getattr(args, "command", "?"),
        "каталог артефактов": DIST_DIR,
        "пакет DMM": BUNDLE_DIR,
        "VPK": VPK_SOURCE,
        **{name: getattr(args, name) for name in sorted(vars(args))
           if name not in ("command", "func", "game_path", "dist")},
    })
    try:
        code = args.func(args)
        journal.summary(
            status=("готово" if code == 0 else f"завершено с кодом {code}"),
            artifacts=[VPK_SOURCE] if VPK_SOURCE.is_file() else [],
        )
        return code
    except (SystemExit, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            print("прервано пользователем")
            return 130
        if isinstance(error, KeyboardInterrupt):
            trace_log.summary(status="прервано пользователем")
        message = getattr(error, "code", None) or str(error)
        if isinstance(message, str) and message:
            print(f"ОШИБКА: {message}")
            if not isinstance(error, KeyboardInterrupt):
                trace_log.exception(error, where="установка мода")
                trace_log.summary(status=f"ошибка: {message.splitlines()[0]}")
        return 2
    except (gameinfo.GameInfoError, vpk.VpkError, dmm_bundle.BundleError, OSError) as error:
        print(f"ОШИБКА: {error}")
        trace_log.exception(error, where="установка мода")
        trace_log.summary(status=f"ошибка: {error}")
        return 2
    finally:
        trace_log.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
