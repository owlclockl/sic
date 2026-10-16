# Deadlock :: Bhop Trainer — HUD-аддон

Настоящая панель поверх игры: подсказки по банихопу прямо в HUD, в стиле интерфейса Deadlock.
Собирается из исходников Valve Panorama (`.vxml` + `.vcss`) в аддон-`.vpk` и ставится в игру
через **Deadlock Mod Manager** — вручную ничего править не нужно.

> **Честно о статусе.** Мод собирается из исходников этого репозитория, но для работоспособного
> VPK нужен `resourcecompiler.exe` из CSDK 12: движок Deadlock не грузит сырые `.css`/`.xml`
> из аддона, ему нужны скомпилированные `.vcss_c`/`.vxml_c`. Поэтому шаг компиляции — Windows +
> CSDK, а `prepare`, `pack`, `dmm`, `report` и вся установка работают где угодно (Linux/Proton тоже).
> CSDK больше **не надо скачивать руками**: если компилятора нет, сборщик сам качает и распаковывает
> Reduced CSDK 12 (`tools/csdk_setup.py`, отключается ключом `--no-auto-csdk` / `--no-csdk-download`).
> Ванильный `hud.xml` тоже добывается сам: в паках игры Valve держит только **скомпилированный**
> `panorama/layout/hud.vxml_c`, поэтому исходник берётся из файла рядом с игрой, из кэша прошлых
> сборок или с зеркала депо GameTracking (`tools/vanilla_hud.py`; отключается `--no-hud-download`,
> а после патча игры обновляется ключом `--refresh-hud-xml`).
> Скриптов динамики тут нет (панель статичная), поэтому она не мешает игре, но и не переключает
> шаги сама — это делается правкой текста или добавлением vjs-скрипта.

---

## Windows: сборка одним батником

Если ничего вводить не хочется — рядом с этим README лежит `build.bat`. Двойной клик,
дальше он делает всё сам:

* находит **Python 3.9+** (нет — предлагает поставить через `winget`
  или открывает страницу загрузки; Store-заглушку `WindowsApps\python.exe` отбрасывает);
* находит **Deadlock** — реестр Steam, все библиотеки из `libraryfolders.vdf`
  и типичные пути на дисках `C:`–`K:`; оттуда же берётся ванильный `hud.xml`;
* находит **CSDK 12** — `--csdk`, переменные `DEADLOCK_CSDK` / `CSDK_ROOT` / `CSDK_PATH`,
  `~/Deadlock SDK`, `~/Desktop|Downloads|Documents\Deadlock SDK`, корни дисков.
  Найденный путь проверяется на `game\bin_cs2\win64\resourcecompiler.exe`, а если это
  OneDrive — предупреждает: оттуда компилятор обычно падает;
* если CSDK так и не нашёлся, **скачивает и распаковывает его сам** (пункт `[1]` в меню,
  он же вариант по умолчанию): Reduced CSDK 12, в каталог вне OneDrive/Desktop/Documents,
  с догрузкой при обрыве и кэшем архива, чтобы не качать гигабайты дважды;
* ванильный `hud.xml` берёт сам (файл в дереве игры/CSDK, кэш или зеркало депо) — в паках игры
  исходников нет, там только `panorama/layout/hud.vxml_c`;
* собирает VPK и пакет DMM, а в конце предлагает выложить пакет, поставить мод
  напрямую, скопировать `.cfg` тренажёра, проверить установку или открыть отчёт.

```bat
build.bat
build.bat --game-path "D:\Steam\steamapps\common\Deadlock" --csdk "C:\Deadlock SDK"
build.bat --clean --test -y
build.bat --clean -y --csdk-install-dir "C:\DeadlockModding"
```

| Ключ | Что делает |
|---|---|
| `--game-path PATH` | папка Deadlock (или её `game\`, или `game\citadel`) |
| `--csdk PATH` | папка CSDK 12 с `resourcecompiler.exe` |
| `--no-csdk-download` | ничего не качать: только спросить путь к уже распакованному CSDK |
| `--csdk-install-dir PATH` | куда распаковать скачанный CSDK (по умолчанию `%LOCALAPPDATA%\DeadlockModding\Reduced_CSDK_12`) |
| `--csdk-url URL` | своя ссылка на архив CSDK (если официальная поменялась) |
| `--csdk-redownload` | игнорировать кэш и скачать архив заново |
| `--hud-xml FILE` | уже сохранённый ванильный `panorama/layout/hud.xml` (если игры нет) |
| `--hud-xml-url URL` | своя ссылка на ванильный `hud.xml` (по умолчанию — зеркало депо GameTracking) |
| `--refresh-hud-xml` | не брать `hud.xml` из кэша, взять заново (после патча игры) |
| `--no-hud-download` | вообще не скачивать `hud.xml`: файл на диске, дерево игры/CSDK и кэш |
| `--steps LIST` | `prepare,compile,pack,dmm,report` (по умолчанию все) |
| `--out DIR` | куда положить артефакты (по умолчанию `dist`) |
| `--version X.Y.Z` | версия мода |
| `--allow-uncompiled` | собрать без компилятора: в игре такой мод не заработает |
| `--clean` | сначала удалить `build\` и `dist\` |
| `--test` | перед сборкой прогнать тесты сборщика |
| `-y` | совсем без вопросов: сам ставит Python и CSDK, не ждёт нажатия клавиши (для CI) |

Неизвестные ключи пробрасываются в `tools\build_mod.py` как есть
(например `--compiler-cmd` или `--addon-name`).

Сам батник намеренно **только ASCII** и не переключает кодовую страницу: так он
одинаково читается на любой локали Windows, а русский текст в консоли идёт от
`build_mod.py` (Python 3.6+ пишет в консоль через Unicode). Если будешь править
файл — сохраняй его в ASCII/UTF-8 и обязательно с переводами строк CRLF
(это фиксирует `.gitattributes`).

---

## Быстрый путь: собрать → поставить через DMM

```bash
cd bhop-hud-mod

# 1. Сборка: ванильный hud.xml берётся прямо из паков установленной игры
python3 tools/build_mod.py --game-path "/mnt/games/Steam/steamapps/common/Deadlock" \
                           --csdk "/mnt/c/Deadlock SDK"

# 2. Выложить пакет в удобное место и подсказать шаги
python3 tools/install_dmm.py stage --open
```

Дальше в **Deadlock Mod Manager**: *Mods → Import → Open interchange bundle* → выбрать
`mod-interchange.json` из выложенной папки → закрыть Deadlock → **Launch modded**.
DMM сам добавит `Game citadel/addons` в `gameinfo.gi` и положит мод в `citadel/addons` как
`pakNN_dir.vpk`.

Проверка, что всё встало:

```bash
python3 tools/install_dmm.py verify
```

Команда смотрит в `game/citadel/addons`, находит там наш VPK, читает его метаданные и проверяет
строку поиска в `gameinfo.gi`. Если игру нужно вернуть в исходное состояние —
`python3 tools/install_dmm.py uninstall --restore-gameinfo`.

### Полезные варианты запуска сборщика

| Задача | Команда |
|---|---|
| Собрать всё (по умолчанию: `prepare,compile,pack,dmm,report`) | `python3 tools/build_mod.py --game-path … --csdk …` |
| Есть только «CSDK» без игры, ванильный `hud.xml` лежит файлом | `… --hud-xml /path/to/hud.xml --csdk …` |
| CSDK не установлен — пусть сборщик поставит его сам | `… --game-path …` (по умолчанию так и есть) |
| Категорически без сети | `… --game-path … --no-auto-csdk --no-hud-download` |
| После патча игры: взять ванильный `hud.xml` заново | `… --game-path … --refresh-hud-xml` |
| Своя ссылка на ванильный `hud.xml` | `… --hud-xml-url https://…/hud.xml` |
| Игра есть, компилятор не нужен (проверить пайплайн) | `… --game-path … --allow-uncompiled` |
| Собрать с нуля, удалив `build/` и `dist/` | `… --game-path … --clean` |
| Только поставить CSDK, ничего не собирая | `python3 tools/csdk_setup.py` |
| Только подготовить дерево аддона + патч `hud.xml` | `… --steps prepare` |
| Компилятор кладёт результат в нестандартное место | `… --compiled-from /путь/к/дереву` |
| Упаковать дерево, скомпилированное вручную в CSDK | `… --csdk PATH --steps prepare,pack` |
| Итог в JSON (для CI) | `… --json` (логи уходят в stderr, в stdout — только JSON) |
| Другой номер/название сборки | `… --version 1.2.0 --title "Bhop Trainer (test)" --addon-name bhopt_test` |

Коды возврата: `0` — всё собрано, `2` — ошибка, `3` — сборка остановлена на компиляции
(CSDK не найден и не скачался, либо автоскачивание отключено; артефакты «только исходники»
не создаются, если не передан `--allow-uncompiled`).

### Откуда берётся ванильный `hud.xml`

Мод переопределяет `panorama/layout/hud.xml`, но **в паках игры исходника нет**: Valve кладёт туда
только скомпилированный `panorama/layout/hud.vxml_c`. Поэтому `prepare` ищет файл по цепочке
(`tools/vanilla_hud.py`) и берёт первый подходящий:

1. `--hud-xml FILE` — если путь указан руками, берётся он (и только он);
2. `panorama/layout/hud.xml` файлом в дереве игры или CSDK (так лежат выгрузки Source 2 Viewer);
3. сырой `panorama/layout/hud.xml` внутри паков игры/CSDK (старые сборки, где Valve ещё клал исходники);
4. кэш прошлых сборок — `build/vanilla/hud.xml`; он помнит, при какой сборке игры взят
   (`build/vanilla/hud_source.json`), и если игра обновилась, файл считается устаревшим;
5. зеркало депо `SteamTracking/GameTracking-Deadlock`, где panorama-ресурсы выложены
   распакованными (Source 2 Viewer → XML). Ветка `master`, а источников три:
   `raw.githubusercontent.com`, CDN jsdelivr и GitHub API (отдаёт тот же файл
   в JSON/base64) — если один хост не отвечает, берётся со следующего, так что
   сборка проходит и там, где raw-хост недоступен. Отключается `--no-hud-download`,
   принудительное обновление — `--refresh-hud-xml`.

Найденный файл проверяется (это правда layout: `<styles>`, `<CitadelHud>`, закрывающие теги,
разбирается как XML), а в отчёт попадает источник, ссылка и номер сборки игры. Если не сработало
ничего, сборщик не гадает, а объясняет: что искал, что в паках вместо исходника и как достать
файл вручную (Source 2 Viewer → `pak01_dir.vpk` → `panorama/layout/hud.vxml_c` → Decompile →
сохранить как `hud.xml`).

### Автоустановка CSDK: что именно происходит

Шаг 0 сборки (`tools/csdk_setup.py`) включается, только если компилятор реально нужен
(есть шаг `compile`, нет `--compiler-cmd` и `--allow-uncompiled`):

1. Сначала — обычный поиск: `--csdk`, переменные окружения, типичные каталоги. Нашли — ничего
   не качаем: повторная сборка ничего не скачивает.
2. Не нашли — качаем Reduced CSDK 12 с Google Диска по ссылке со страницы
   <https://deadlockmodding.pages.dev/modding-tools/csdk-12>: страница подтверждения
   обходится сама, пользовательский агент и cookie подставляются сами.
3. Пишем в `<каталог установки>/../csdk-cache/download.part`, поэтому **обрыв связи не
   обнуляет прогресс**: следующий запуск догружает хвост по HTTP Range. Архив из кэша
   переиспользуется, `--csdk-redownload` заставляет качать заново.
4. Распаковываем `.zip` / `.tar.*` средствами Python; для `.7z` / `.rar` берём 7-Zip, а если
   его нет — ставим через `winget` (отключается `--no-tool-install`).
5. Приводим раскладку к корню CSDK: если внутри архива лежит папка `Reduced_CSDK_12`,
   её содержимое поднимается на уровень выше. Проверяем, что на месте
   `game/bin_cs2/win64/resourcecompiler.exe`, и только после этого подменяем прошлую установку.
6. Куда ставим: `%LOCALAPPDATA%\DeadlockModding\Reduced_CSDK_12` (на Windows) —
   пользовательский каталог, не синхронизируемый облаком. Путь с `OneDrive`, `Desktop`,
   `Documents`, `Dropbox` и т.п. **отвергается**: оттуда инструменты CSDK падают.

Отдельный запуск (только установка, без сборки):

```bash
python3 tools/csdk_setup.py                       # найти или скачать
python3 tools/csdk_setup.py --install-dir C:\csdk12
python3 tools/csdk_setup.py --url https://example.org/Reduced_CSDK_12.zip --sha256 <хеш>
python3 tools/csdk_setup.py --no-download         # только поиск (код 3, если не найден)
python3 tools/csdk_setup.py --json                # итог машинно-readable
```

---

## Что производит сборщик

| Артефакт в `dist/` | Что это |
|---|---|
| `bhop_trainer_dir.vpk` | Готовый аддон: `panorama/layout/hud.vxml_c`, `panorama/styles/bhop_trainer.vcss_c` + метаданные |
| `dmm-bundle/` | Пакет для Deadlock Mod Manager (`mod-interchange.json` + `files/…`) |
| `bhop_trainer-dmm.zip` | Тот же пакет архивом: можно распаковать (для импорта) или перетащить целиком в DMM в «Add Mods» |
| `SHA256SUMS.txt` | Контрольные суммы артефактов |
| `BUILD_REPORT.md` | Отчёт: статус, входные хеши, что попало в VPK, заметки и предупреждения |

Рабочие файлы (не коммитятся): `build/content/citadel_addons/<addon>` — дерево аддона для CSDK,
`build/game/citadel_addons/<addon>` — скомпилированные ресурсы, `build/vanilla/hud.xml` — копия
ванильного файла, из которого делался патч (она же кэш для следующих сборок),
`build/vanilla/hud_source.json` — откуда и с какой сборки игры этот файл,
`build/build_state.json` — состояние сборки.

### Как устроена установка через DMM

Deadlock Mod Manager (DMM) умеет принимать сторонние моды двумя путями, и сборщик готовит оба:

1. **Импорт пакета** — формат «Deadlock Mod Interchange» из RFC самого DMM: каталог с
   `mod-interchange.json` и `files/<папка>/<файл>.vpk`. В DMM это
   *Import → Open interchange bundle* → выбрать манифест.
2. **Перетаскивание архива** — zip бросается в раздел *Add Mods*, DMM распаковывает его
   и находит `.vpk` рекурсивно.

Идентификаторы выбраны так, чтобы повторный импорт распознавался как дубликат, а мод оставался
узнаваемым без сети:

* `key` = `local:<sha256 VPK>` — так спецификация предписывает идентифицировать локальные моды;
* `origin.localId` = `local-<uuid>`, uuid выводится из ключа ровно тем же способом, что и в
  импортере DMM (`uuid_from_seed`), поэтому его принимает `SubmissionRef::parse_slug`;
* внутрь VPK вшиты метаданные **vpk-modinfo v1** (`addoninfo.txt` + `modinfo.json`): название,
  автор, версия и «канонический» хеш файла до прошивки. Их показывают браузеры VPK, и по ним мод
  опознаётся другими менеджерами (например Grimoire).

### Запасной путь без менеджера

Если DMM недоступен (или нужно поставить вручную):

```bash
python3 tools/install_dmm.py direct --dry-run   # посмотреть план
python3 tools/install_dmm.py direct             # свободный слот pakNN_dir.vpk + строка в gameinfo.gi
```

`direct` копирует VPK в `game/citadel/addons/pakNN_dir.vpk` (первый свободный слот 1–99) и
добавляет в `gameinfo.gi` строку `Game citadel/addons` со своим маркером-комментарием,
предварительно сохранив копию `gameinfo.gi.bhop-bak-<дата>`. Перед записью проверяется баланс
фигурных скобок: сломанный `gameinfo.gi` не даёт игре запуститься, поэтому при подозрении правка
отменяется. Откат — `install_dmm.py uninstall`, при необходимости `--restore-gameinfo`.

Конфиги самого тренажёра (`.cfg` из соседней папки `bhop-trainer/`) в VPK не влезают и менеджером
не ставятся, поэтому для них отдельная команда:

```bash
python3 tools/install_dmm.py cfg              # скопировать .cfg в game/citadel/cfg
python3 tools/install_dmm.py cfg --dry-run    # только показать, что будет скопировано
```

---

## Зачем нужен CSDK: компиляция Panorama

В layout-файлах ресурсы адресуются «исходными» именами:

```xml
<include src="s2r://panorama/styles/bhop_trainer.vcss" />
```

но искать движок будет `bhop_trainer.vcss_c` — скомпилированный ресурс. Ровно поэтому работает
шаг `compile`:

| Исходник | Скомпилированный ресурс |
|---|---|
| `panorama/layout/hud.xml` | `panorama/layout/hud.vxml_c` |
| `panorama/styles/bhop_trainer.vcss` | `panorama/styles/bhop_trainer.vcss_c` |
| `.js` / `.vjs` | `.vjs_c` |
| `.svg`, `.png`, `.wav` | `.vsvg_c`, `.vtex_c`, `.vsnd_c` |

Сборщик запускает `resourcecompiler.exe` из CSDK (`<CSDK>/game/bin_cs2/win64`), складывая исходники
в `<CSDK>/content/citadel_addons/<addon>` и забирая результат из `<CSDK>/game/citadel_addons/<addon>`.
Пути CSDK ищутся в `--csdk`, переменных `DEADLOCK_CSDK` / `CSDK_ROOT` / `CSDK_PATH` и типичных местах;
скачать CSDK 12: <https://deadlockmodding.pages.dev/modding-tools/csdk-12> (не распаковывать в OneDrive).
Руками этого делать не нужно — как именно сборщик ставит CSDK сам, описано выше, в разделе
«Автоустановка CSDK».

Без компилятора:

* обычный запуск останавливается с кодом `3` и объяснением (это честнее, чем отдать нерабочий VPK);
* с `--allow-uncompiled` собираются артефакты **только для проверки пайплайна** — VPK с сырыми
  исходниками кладётся в `dist/sources-only/`, пакет для DMM не создаётся вообще.

Свой компилятор (например, обёртку над `resourcecompiler.exe` или заглушку в CI) можно подставить
через `--compiler-cmd` с плейсхолдерами
`{compiler} {sdk} {game} {addon} {source} {content_root} {output_root}`;
именно так работают тесты в `tests/`.

### Где сборщик ищет результат компиляции

`resourcecompiler` из CSDK игнорирует «куда положить» и пишет в своё game-дерево, поэтому шаг
compile перебирает известные места и берёт первое, где лежит непустой ресурс:

1. `build/game/citadel_addons/<addon>` — если компилятор вызван обёрткой с явным output-каталогом;
2. `--compiled-from DIR` — если ты указал его сам;
3. `<CSDK>/game/citadel_addons/<addon>` — штатное место CSDK;
4. дерево самой игры (`<Deadlock>/game`) — так получается при компиляции прямо в дереве игры.
   Отсюда ресурсы берутся, **только если они появились или обновились в этом запуске**, и после
   копирования удаляются: сборка не должна оставлять мусор в файлах игры.

Если ресурс не найден нигде, сборка останавливается с подсказкой указать `--compiled-from`.
Дополнительно: если скомпилированный ресурс оказался старее своего исходника, сборщик
предупреждает об этом — значит, компиляция его не обновила.

---

## Инструменты

| Файл | Назначение |
|---|---|
| `build.bat` | Windows: двойной клик — сам ищет Python, Deadlock и CSDK, собирает и ставит |
| `tools/build_mod.py` | Автосборщик: `prepare → compile → pack → dmm → report` |
| `tools/install_dmm.py` | Установщик: `check`, `stage`, `verify`, `direct`, `uninstall`, `cfg` |
| `tools/vpk.py` | VPK v2: запись, чтение, проверка; паки игры читает через `mmap`, учитывает preload и проверяет CRC32 |
| `tools/modinfo.py` | Метаданные `vpk-modinfo v1` внутрь VPK (`addoninfo.txt` + `modinfo.json`) |
| `tools/vanilla_hud.py` | Где взять ванильный `hud.xml`: файл, дерево игры/CSDK, паки, кэш, зеркало депо |
| `tools/hud_patch.py` | Патч `hud.xml`: вставка панели и include стилей, идемпотентно, с откатом |
| `tools/dmm_bundle.py` | Пакет «Deadlock Mod Interchange» и zip для DMM |
| `tools/paths.py` | Поиск Deadlock / CSDK / данных DMM, проверка запущенного процесса игры |
| `tools/csdk_setup.py` | Автоустановка CSDK 12: поиск, скачивание с догрузкой, распаковка, проверка компилятора |
| `tools/gameinfo.py` | Проверка и правка `gameinfo.gi` с резервными копиями |

Требуется только Python 3.9+ (стандартная библиотека, без зависимостей).

## Тесты

```bash
cd bhop-hud-mod
python3 -m unittest discover -s tests -p "test_*.py"
```

Проверяется: байтовая раскладка VPK и её соответствие тому, что читает DMM, чтение
многокомпонентных паков (preload, секции MD5/подписи, `mmap`), правила `vpk-modinfo`,
патч `hud.xml` (идемпотентность, CRLF, откат), поиск ванильного `hud.xml` (цепочка источников,
кэш, скачивание и внятная ошибка, когда исходника нет нигде — сеть в тестах подменяется),
пакет interchange (ключи, пути, хеши), установщик (gameinfo, слоты, dry-run) и сквозная сборка
через `fixtures/fake_compiler.py`. Фикстуры написаны в этом репозитории — игровые файлы Valve
(в том числе настоящий `hud.xml`) не коммитятся, а берутся из установленной игры.

---

## Ручная сборка (Windows + CSDK, без автосборщика)

Если нужно повторить всё руками — шаги те же, что делает `build_mod.py`:

1. **Поставить CSDK 12** — <https://deadlockmodding.pages.dev/modding-tools/csdk-12>
   (не распаковывать в OneDrive: Desktop/Documents ломают права и инструменты падают).
2. **Создать аддон** в `csdkcfg.exe`: имя строчными буквами без пробелов, например `bhop_trainer`.
   Появятся `content/citadel_addons/bhop_trainer` и `game/citadel_addons/bhop_trainer`.
3. **Достать vanilla `hud.xml`**: исходного `.xml` в паках игры нет, там лежит скомпилированный
   `panorama/layout/hud.vxml_c`. Открой Source 2 Viewer → `game/citadel/pak01_dir.vpk` →
   `panorama/layout/hud.vxml_c` → *Decompile*/*Export* и сохрани как `hud.xml` в
   `content/citadel_addons/bhop_trainer/panorama/layout/hud.xml`.
   (Так же можно взять уже распакованный файл из зеркала депо
   `SteamTracking/GameTracking-Deadlock`.)
4. **Вставить разметку**: скопировать содержимое `src/bhop_trainer.vxml` в конец этого файла,
   **перед** закрывающим `</CitadelHud>`.
5. **Подключить стили**: в начале того же `hud.xml`, внутри блока `<styles>`, добавить строку
   ```xml
   <include src="s2r://panorama/styles/bhop_trainer.vcss" />
   ```
   а сам файл `src/bhop_trainer.vcss` положить в
   `content/citadel_addons/bhop_trainer/panorama/styles/`.
6. **Скомпилировать**: в лончере CSDK — правый клик по аддону → *Compile All Assets*
   (или Asset Browser → правый клик по файлам → *Recompile > Full*).
   Ожидаемые результаты: `game/citadel_addons/bhop_trainer/panorama/layout/hud.vxml_c`
   и `panorama/styles/bhop_trainer.vcss_c`.
7. **Упаковать**: правый клик по аддону → *Create VPK*, либо CS2 Workshop Manager намеренно
   уронить на Submit (VPK появится в `game/citadel_addons`). Вариант без Windows — взять
   скомпилированное дерево и упаковать его сборщиком:
   `python3 tools/build_mod.py --csdk <CSDK> --steps prepare,pack`; либо просто скопировать
   `game/citadel_addons/<addon>` в `build/game/citadel_addons/<addon>` и запустить
   `… --steps pack` без `--allow-uncompiled`.
8. **Поставить в игру**: положить VPK в `Deadlock/game/citadel/addons` под именем
   `pakNN_dir.vpk` (номер 01–99), `gameinfo.gi` должен содержать `Game citadel/addons`
   в `SearchPaths`. Полностью выйти из игры и запустить заново. Проще —
   `python3 tools/install_dmm.py direct`.

---

## Палитра (взято из `citadel_base_styles.css` самой игры)

| Токен | HEX | Где взят |
|---|---|---|
| `bhoptText` | `#FFEFD7` | `baseText` — основной текст игры |
| `bhoptGold` | `#FFED79` | `goldColor` — акценты, ключи |
| `bhoptSoul` | `#70F8C1` | `soulColor` — «зелёный душ» (резерв под успех) |
| `bhoptEnemy` | `#FF410D` | `colorEnemy` — предупреждения |
| `bhoptBackdrop` | `#10130D` | `offBlack` — фон HUD |
| Шрифты | `Reaver`, `Radiance`, `Retail Text Demo` | `@define` из игрового CSS |

## Настройка вида

Правки — прямо в `src/bhop_trainer.vcss` (после правки — пересобрать):

| Что хочется | Что менять |
|---|---|
| Другая позиция панели | `.bhopt_panel` → `margin-left` / `margin-top` (и те же числа в `.bhopt_corner_*`) |
| Ширина | `.bhopt_panel` → `width`, плюс `margin-left` правых скобок = `margin-left` + ширина + 22px |
| Убрать пульсацию | закомментировать `animation-name` в `.bhopt_stepno` и `.bhopt_diamond` |
| Другой акцент (soul-зелёный) | `@define bhoptGold: #70F8C1;` |
| Больше строк подсказок | добавить блоки `<Panel class="bhopt_row">` в `src/bhop_trainer.vxml` |
| Скрыть панель на время матча | поставить `visibility: collapse;` в `.bhopt_panel` и в `.bhopt_corner` |

## Динамика (по желанию)

Сейчас панель статична. Чтобы переключать шаги из игры, нужен скрипт:
`panorama/scripts/bhop_trainer.vjs` + `<include src="s2r://panorama/scripts/bhop_trainer.vjs" />`
внутри `<scripts>` и консольная команда/`CitadelHud`-хук. Тогда `J` будет листать содержимое,
а `M` — показывать блок скорости. Скрипты кладутся в `src/`, автоматически попадают в дерево
аддона и компилируются в `.vjs_c` — сборщик менять не нужно.

> Важно: скрипты не должны давать игрового преимущества и не читают состояние матча — только
> ведут твой собственный тренажёр, поэтому панель остаётся клиентской косметикой.

---

## Траблшутинг

| Симптом | Причина / решение |
|---|---|
| Панели нет в игре | Прогони `python3 tools/install_dmm.py verify`: он показывает, найден ли VPK в `citadel/addons` и есть ли строка `Game citadel/addons` в `gameinfo.gi` |
| Мод импортировался в DMM, но не работает | Скорее всего игра была запущена: DMM не патчит `gameinfo.gi` при живом `citadel.exe`. Закрой игру и нажми **Launch modded** |
| Сборка падает: «не нашёл ванильный panorama/layout/hud.xml» | В паках игры нет исходника — там только скомпилированный `hud.vxml_c`. Дай сети доступ (сборщик сам скачает файл с зеркала депо) либо передай его через `--hud-xml`; см. раздел «Откуда берётся ванильный `hud.xml`» |
| Нужен ванильный `hud.xml` без сети | Возьми файл любым способом (Source 2 Viewer, зеркало депо на другой машине) и передай: `--hud-xml файл` или `--hud-xml-url file:///путь/к/hud.xml`; кэш `build/vanilla/hud.xml` переиспользуется сам |
| Все зеркала недоступны (блокировка/фаервол) | Сборщик перебирает `raw.githubusercontent.com`, `cdn.jsdelivr.net` и `api.github.com`; если все три молчат — скачай `game/citadel/pak01_dir/panorama/layout/hud.xml` из `SteamTracking/GameTracking-Deadlock` на другой машине и передай `--hud-xml` |
| После патча игры панель поехала / HUD сломался | Ванильный `hud.xml` устарел: `--refresh-hud-xml` и пересборка. Сборщик ещё и предупредит, если файл из кэша старше установленной сборки игры |
| Сборка встала с кодом 3 | CSDK не нашёлся и не скачался: проверь сеть, потом `--csdk` (или переменную `DEADLOCK_CSDK`). Скачать руками: deadlockmodding.pages.dev/modding-tools/csdk-12 |
| Сборка качает CSDK, но не может | Ссылка на файл Google Диска могла измениться: возьми свежую со страницы CSDK 12 и передай её через `--csdk-url URL`. Свой распакованный каталог — `--csdk PATH` |
| Скачивание обрывается | Не страшно: запусти сборку снова — докачает с того же места (`csdk-cache/download.part`). Начать заново: `--csdk-redownload` |
| Архив CSDK не распаковывается (`.7z`/`.rar`) | Нужен 7-Zip: `winget install -e --id 7zip.7zip` (сборщик пытается поставить его сам), либо распакуй руками и укажи `--csdk PATH` |
| Сборщик отказывается ставить CSDK в мой каталог | В пути есть OneDrive/Desktop/Documents/Dropbox — там ломаются права. Задай `--csdk-install-dir PATH` вне синхронизации |
| Распаковка CSDK падает на длинных путях | Ограничение Windows MAX_PATH: задай короткий каталог, например `--csdk-install-dir C:\csdk12` |
| Панель есть, стилей нет | Не скомпилировался `.vcss`: проверь в отчёте, что в VPK есть `panorama/styles/bhop_trainer.vcss_c` |
| В VPK только исходники | Был запуск с `--allow-uncompiled`: такой аддон в игре не работает, пересобери с CSDK (сейчас он ставится сам) |
| Два HUD-мода конфликтуют | Оба кладут свой `panorama/layout/hud.xml`; побеждает меньший номер `pakNN`. Держи этот мод в отдельном слоте (DMM умеет менять порядок) |
| Текст «плывёт», буквы не те | Проверь, что шрифт — `Reaver`/`Radiance`; иначе упадёт на дефолтный |
| `build.bat` пишет «Python 3.9+ не найден» | Поставь Python (вариант [1] в меню батника — через `winget`) и перезапусти батник: в уже открытом окне PATH мог не обновиться |
| `build.bat` не видит игру | Steam стоит не в `Program Files` — передай путь руками: `build.bat --game-path "…"`, либо выбери пункт [1] в меню |
| `build.bat` закрывается, не успев ничего показать | Запусти его из уже открытого `cmd.exe` (а не двойным кликом) — так окно не закроется |
| После патча игры всё сломалось | Valve обновляет `hud.xml`: игра читает новый layout, а наш мод — старый. Пересобери с `--game-path` (ванильный файл вытянется заново) и переустанови |
| Игра не стартует, `KeyValues`-ошибка | Сломан `gameinfo.gi`. `install_dmm.py uninstall --restore-gameinfo` вернёт копию, либо Steam → «Проверить целостность файлов» |
| Хочу вернуть как было | `python3 tools/install_dmm.py uninstall --restore-gameinfo` (и удалить мод в DMM, если ставил через него) |
