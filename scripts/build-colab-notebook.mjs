/**
 * Собирает готовый notebook для Google Colab: сервер онлайн-режима
 * Reveal Atlas, который запускается прямо из Google Drive и получает
 * публичный адрес через Cloudflare Quick Tunnel.
 *
 *   node scripts/build-colab-notebook.mjs
 *
 * Сам notebook кладётся в deploy/ и загружается в Google Drive
 * («Открыть с помощью → Дополнительно → Google Colaboratory»).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.join(ROOT, 'deploy');
const OUT_FILE = path.join(OUT_DIR, 'RevealAtlas-Online-Colab.ipynb');

const SERVER_FILES = ['ws.mjs', 'rooms.mjs', 'online.mjs', 'index.mjs'];

function readServerFiles() {
  const files = {};
  for (const name of SERVER_FILES) {
    const full = path.join(ROOT, 'server', name);
    if (!fs.existsSync(full)) throw new Error(`Не найден файл сервера: ${full}`);
    files[name] = fs.readFileSync(full).toString('base64');
  }
  return files;
}

const INSTALL_CELL = `# --- 1. Подготовка: Node.js и cloudflared -------------------------------
# Этой ячейке нужно 20-40 секунд. Всё ставится внутрь Colab, ничего не меняется в системе.
import os, shutil, subprocess, sys, urllib.request, zipfile

WORK = '/content/reveal-atlas-online'
os.makedirs(f'{WORK}/server', exist_ok=True)

def sh(cmd):
    print('$', cmd)
    subprocess.run(cmd, shell=True, check=True)

# Node.js 22 (в Colab его нет, а системный из apt слишком старый)
NODE_DIR = '/content/node'
if not os.path.exists(f'{NODE_DIR}/bin/node'):
    sh('wget -q https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-x64.tar.xz -O /content/node.tar.xz')
    sh('mkdir -p /content/node && tar -xf /content/node.tar.xz -C /content/node --strip-components=1')
os.environ['PATH'] = f'{NODE_DIR}/bin:' + os.environ['PATH']
print('node:', subprocess.run(['node', '-v'], capture_output=True, text=True).stdout.strip())

# cloudflared — бесплатный туннель без регистрации
if not os.path.exists('/content/cloudflared'):
    sh('wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /content/cloudflared')
    sh('chmod +x /content/cloudflared')
print('cloudflared готов')
`;

const FILES_CELL_PREFIX = `# --- 2. Файлы сервера (встроены в notebook, интернет не нужен) -----------
# Это тот самый код из репозитория: WebSocket-сервер, комнаты, HTTP API.
import base64, os

FILES = `;

const FILES_CELL_SUFFIX = `

WORK = '/content/reveal-atlas-online'
for name, b64 in FILES.items():
    path = os.path.join(WORK, 'server', name)
    with open(path, 'wb') as f:
        f.write(base64.b64decode(b64))
    print('записан', path)
`;

const RUN_CELL = `# --- 3. Запуск: сервер + публичный адрес --------------------------------
# Оставьте эту ячейку работающей, пока идёт игра. Остановка (■) гасит сервер.
import os, re, shutil, subprocess, sys, threading, time
from IPython.display import HTML, display

PORT = 8080                 # порт внутри Colab (наружу откроется туннель)
USE_DRIVE = True            # True — комнаты сохраняются в вашем Google Drive
BUILD_APP = False           # True — собрать и раздавать само приложение (нужен git-доступ к репозиторию)
REPO_URL = ''               # например 'https://github.com/owlclockl/sic.git' — только для BUILD_APP

WORK = '/content/reveal-atlas-online'
os.environ['PATH'] = '/content/node/bin:' + os.environ['PATH']

# Куда складывать комнаты и картинки миров.
DATA_DIR = f'{WORK}/data'
if USE_DRIVE:
    try:
        from google.colab import drive
        drive.mount('/content/drive')
        DATA_DIR = '/content/drive/MyDrive/RevealAtlas-Online'
        os.makedirs(DATA_DIR, exist_ok=True)
        print('Комнаты будут храниться в Google Drive:', DATA_DIR)
    except Exception as err:
        print('Не удалось подключить Google Drive, храним внутри Colab:', err)
        USE_DRIVE = False

if BUILD_APP and REPO_URL:
    subprocess.run(f'rm -rf {WORK}/app && git clone --depth 1 {REPO_URL} {WORK}/app', shell=True, check=True)
    subprocess.run(f'cd {WORK}/app && npm ci && npm run build', shell=True, check=True)
    APP_DIST = f'{WORK}/app/dist'
else:
    APP_DIST = f'{WORK}/dist'
    if not os.path.exists(f'{APP_DIST}/index.html'):
        APP_DIST = ''   # приложение откроется с любого другого адреса, сервер всё равно работает

# Сервер
server = subprocess.Popen(
    ['node', 'server/index.mjs', '--port', str(PORT), '--data', DATA_DIR] + (['--dist', APP_DIST] if APP_DIST else []),
    cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
)

def pump_server():
    for line in iter(server.stdout.readline, ''):
        print('[сервер]', line, end='')

threading.Thread(target=pump_server, daemon=True).start()

# Публичный адрес
tunnel = subprocess.Popen(
    ['/content/cloudflared', 'tunnel', '--url', f'http://127.0.0.1:{PORT}', '--no-autoupdate'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
)
public_url = None
for _ in range(120):
    line = tunnel.stdout.readline()
    if not line:
        break
    found = re.search(r'https://[a-z0-9-]+\\.trycloudflare\\.com', line)
    if found:
        public_url = found.group(0)
        break

if not public_url:
    print('Не удалось получить публичный адрес. Сервер всё равно работает локально внутри Colab.')
else:
    print()
    print('=' * 72)
    print('  ПУБЛИЧНЫЙ АДРЕС СЕРВЕРА:', public_url)
    print('=' * 72)
    print()
    if APP_DIST:
        print('  1. Откройте приложение:', public_url + '/')
    else:
        print('  1. Откройте Reve Atlas на любом устройстве (свой ПК, телефон, ReveAtlas.html).')
    print('  2. Нажмите «Онлайн» → в поле «Сервер» вставьте:', public_url)
    print('  3. «+ Создать комнату» и отправьте другу код или ссылку-приглашение.')
    print()
    display(HTML(f'<a href="{public_url}/" target="_blank" style="font-size:16px">Открыть Reve Atlas: {public_url}</a>'))

# Держим сессию живой, пока оба процесса работают.
try:
    while server.poll() is None and tunnel.poll() is None:
        time.sleep(2)
finally:
    print('Останавливаем сервер и туннель…')
    for proc in (tunnel, server):
        if proc.poll() is None:
            proc.terminate()
`;

function markdownCell(text) {
  return {
    cell_type: 'markdown',
    metadata: {},
    source: text.split('\n').map((line, index, all) => (index === all.length - 1 ? line : `${line}\n`)),
  };
}

function codeCell(text) {
  return {
    cell_type: 'code',
    metadata: {},
    execution_count: null,
    outputs: [],
    source: text.split('\n').map((line, index, all) => (index === all.length - 1 ? line : `${line}\n`)),
  };
}

const notebook = {
  nbformat: 4,
  nbformat_minor: 5,
  metadata: {
    colab: { provenance: [], name: 'RevealAtlas-Online-Colab.ipynb' },
    kernelspec: { name: 'python3', display_name: 'Python 3' },
    accelerator: 'CPU',
  },
  cells: [
    markdownCell(
      [
        '# Reveal Atlas — онлайн-сервер из Google Drive',
        '',
        'Этот notebook запускает **настоящий сервер онлайн-режима** Reveal Atlas',
        '(Node.js + WebSocket) на виртуальной машине Google Colab и открывает к нему',
        '**публичный HTTPS-адрес**. Notebook лежит в вашем Google Drive, поэтому',
        'запускается в любой момент: «Открыть с помощью → Google Colaboratory» → «Среду выполнения» → «Выполнить все».',
        '',
        '**Что важно понимать про Google Drive:** сам Диск — это хранилище файлов,',
        'а не хостинг: он не умеет выполнять код и держать постоянные соединения',
        '(веб-хостинг в Drive отключили ещё в 2016 году). Поэтому сервер запускается',
        'в Colab, а Диск используется для двух вещей: хранения самого notebook и',
        '(по желанию) сохранения комнат между запусками.',
        '',
        '**Ограничения:** адрес `*.trycloudflare.com` временный — он живёт, пока',
        'работает сессия Colab и сама ячейка. Для постоянной игры удобнее поднять',
        'сервер на своём ПК (`npm run online`) или на VPS.',
      ].join('\n'),
    ),
    codeCell(
      [
        '# --- 0. Проверка, что всё на месте ---',
        'import sys',
        'print("Python:", sys.version.split()[0])',
        'print("Ноутбук запущен в Colab:", "google.colab" in sys.modules)',
      ].join('\n'),
    ),
    codeCell(INSTALL_CELL),
    codeCell(FILES_CELL_PREFIX + JSON.stringify(readServerFiles(), null, 2) + FILES_CELL_SUFFIX),
    codeCell(RUN_CELL),
    markdownCell(
      [
        '## Как играть',
        '',
        '1. Друзья открывают приложение Reve Atlas (свой ПК, телефон или файл `RevealAtlas.html`).',
        '2. В панели «Онлайн» в поле «Сервер» вставляют публичный адрес из предыдущей ячейки.',
        '3. Один создаёт комнату, остальные входят по коду или ссылке-приглашению.',
        '4. Все мазки, курсоры, чат и смена сетки синхронны — прогресс общий.',
        '',
        '## Если адрес не появился',
        '',
        '- Cloudflare иногда отвечает не сразу: перезапустите последнюю ячейку.',
        '- В меню Colab проверьте, что сессия не отключилась из-за простоя.',
        '- Альтернатива без туннеля: запустите сервер на ПК (`npm run online`) и',
        '  откройте `http://<IP-вашего-ПК>:8080` — в локальной сети это работает сразу.',
      ].join('\n'),
    ),
  ],
};

fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(OUT_FILE, `${JSON.stringify(notebook, null, 1)}\n`);
const size = fs.statSync(OUT_FILE).size;
console.log(`Notebook собран: ${path.relative(ROOT, OUT_FILE)} (${(size / 1024).toFixed(1)} КБ)`);
console.log('Загрузите его в Google Drive и откройте через «Дополнительно → Google Colaboratory».');
