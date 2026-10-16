# Deadlock :: Bhop Trainer — HUD-аддон

Настоящая панель поверх игры: подсказки по банихопу прямо в HUD, в стиле интерфейса Deadlock.
Собирается из исходников Valve Panorama (`.vxml` + `.vcss`) в аддон-`.vpk` — тем же способом,
что и любые HUD-моды (например QoL HUD).

> **Честно о статусе.** Это исходники, а не готовый `.vpk`: скомпилировать их можно только на
> Windows с установленной Deadlock и CSDK 12 — ни в этой песочнице, ни на Linux-сервере игра
> недоступна. Скриптов динамики тут нет (только статичная панель), поэтому она не мешает игре,
> но и не умеет сама переключать шаги — это делается правкой текста или добавлением vjs-скрипта.

---

## Что внутри

| Файл | Что это |
|---|---|
| `src/bhop_trainer.vxml` | Разметка панели. Вставляется **внутрь игрового `hud.xml`** перед `</CitadelHud>` |
| `src/bhop_trainer.vcss` | Стили: палитра движка, ар-деко рамка, пульсация номера шага |

## Палитра (взято из `citadel_base_styles.css` самой игры)

| Токен | HEX | Где взят |
|---|---|---|
| `bhoptText` | `#FFEFD7` | `baseText` — основной текст игры |
| `bhoptGold` | `#FFED79` | `goldColor` — акценты, ключи |
| `bhoptSoul` | `#70F8C1` | `soulColor` — «зелёный душ» (резерв под успех) |
| `bhoptEnemy` | `#FF410D` | `colorEnemy` — предупреждения |
| `bhoptBackdrop` | `#10130D` | `offBlack` — фон HUD |
| Шрифты | `Reaver`, `Radiance`, `Retail Text Demo` | `@define` из игрового CSS |

---

## Сборка (Windows)

1. **Поставить CSDK 12** — https://deadlockmodding.pages.dev/modding-tools/csdk-12
   (не распаковывать в OneDrive: Desktop/Documents ломают права и инструменты падают).
2. **Создать аддон** в `csdkcfg.exe`: имя строчными буквами без пробелов, например `bhop_trainer`.
   Появятся `content/citadel_addons/bhop_trainer` и `game/citadel_addons/bhop_trainer`.
3. **Достать vanilla `hud.xml`**: Source 2 Viewer → `game/citadel/pak01_dir.vpk` →
   `panorama/layout/hud.xml` → экспортировать в
   `content/citadel_addons/bhop_trainer/panorama/layout/hud.xml`.
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
7. **Упаковать**: правый клик по аддону → *Create VPK*, либо CS2 Workshop Manager (New → Submit
   намеренно упадёт, но VPK появится в `game/citadel_addons`).
8. **Поставить в игру**: положить VPK в `Deadlock/game/citadel/addons` под именем
   `pak02_dir.vpk` (номер 01–99, меньший = выше приоритет), `gameinfo.gi` должен содержать
   `Game citadel/addons` в `SearchPaths`. Полностью выйти из игры и запустить заново.

---

## Настройка вида

Правки — прямо в `src/bhop_trainer.vcss`:

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
`panorama/scripts/bhop_trainer.vjs` + `<include src="s2r://panorama/scripts/bhop_trainer.vjs_c" />`
и консольная команда/`CitadelHud`-хук. Тогда `J` будет листать содержимое, а `M` — показывать
блок скорости. Это следующий шаг: если нужен — скажи, добавлю vjs-скрипт и разметку под него.

> Важно: скрипты не должны давать игрового преимущества и не читают состояние матча — только
> ведут твой собственный тренажёр, поэтому панель остаётся клиентской косметикой.

---

## Траблшутинг

| Симптом | Причина / решение |
|---|---|
| Панели нет в игре | Аддон не подключён: проверь `gameinfo.gi` (`Game citadel/addons`), имя `pak##_dir.vpk`, папку `citadel/addons` |
| Панель есть, стилей нет | Не подключён `.vcss`: проверь `<include src="s2r://panorama/styles/bhop_trainer.vcss" />` и что файл скомпилировался в `_c` |
| Панель в странном месте | Panorama считает от родителя: правь `margin-left/top` в `.bhopt_panel` и синхронно в скобках |
| Текст «плывёт», буквы не те | Проверь, что шрифт — `Reaver`/`Radiance`; иначе упадёт на дефолтный |
| Игра не стартует, ошибка `hud.vxml` | Синтаксис вставки: тег не закрыт или вставлен не внутрь `CitadelHud` |
| После патча всё сломалось | Valve обновляет `hud.xml`; заново достань vanilla-файл, вставь сниппет, пересобери |
