#!/usr/bin/env node
/**
 * Запускает онлайн-сервер Reveal Atlas и, если найден cloudflared,
 * сразу публикует его в интернете через Cloudflare Quick Tunnel.
 *
 *   node scripts/online-tunnel.mjs                # порт 8080
 *   node scripts/online-tunnel.mjs --port 3000
 *
 * cloudflared не входит в проект: установите его один раз
 *   Windows:  winget install --id Cloudflare.cloudflared
 *   macOS:    brew install cloudflared
 *   Linux:    https://github.com/cloudflare/cloudflared/releases
 * Без cloudflared сервер просто работает в локальной сети.
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { attachOnlineServer } from '../server/online.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

function parseArgs(argv) {
  const options = { port: Number(process.env.PORT) || 8080, host: '0.0.0.0', data: null, tunnel: true };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--port' || argv[i] === '-p') options.port = Number(argv[++i]);
    else if (argv[i] === '--host') options.host = argv[++i];
    else if (argv[i] === '--data' || argv[i] === '-d') options.data = argv[++i];
    else if (argv[i] === '--no-tunnel') options.tunnel = false;
    else if (argv[i] === '--tunnel') options.tunnel = true;
  }
  return options;
}

const args = parseArgs(process.argv);
const distDir = path.join(ROOT, 'dist');
const dataDir = path.resolve(args.data ?? path.join(ROOT, 'data', 'online'));

const httpServer = http.createServer();
const online = attachOnlineServer(httpServer, {
  dataDir,
  staticRoot: fs.existsSync(path.join(distDir, 'index.html')) ? distDir : null,
});

httpServer.listen(args.port, args.host, () => {
  const shownHost = args.host === '0.0.0.0' ? 'localhost' : args.host;
  console.log('');
  console.log('  Reveal Atlas — онлайн-сервер запущен');
  console.log(`  Локально:    http://${shownHost}:${args.port}/`);
  console.log(`  Комнаты:     ${dataDir}`);
  if (!fs.existsSync(path.join(distDir, 'index.html'))) {
    console.log('  (приложение не собрано — npm run build; сервер всё равно принимает игроков)');
  }
  console.log('');
});

/** Ищ cloudflared: в .tools/ проекта, в PATH и в типичных местах установки. */
function findCloudflared() {
  const exe = process.platform === 'win32' ? 'cloudflared.exe' : 'cloudflared';
  const candidates = [
    path.join(ROOT, '.tools', exe),
    path.join(ROOT, 'bin', exe),
    path.join(os.homedir(), '.cloudflared', exe),
  ];
  const localAppData = process.env.LOCALAPPDATA;
  const programFiles = process.env.ProgramFiles;
  if (programFiles) candidates.push(path.join(programFiles, 'cloudflared', 'cloudflared.exe'));
  if (localAppData) {
    candidates.push(path.join(localAppData, 'Microsoft', 'WindowsApps', 'cloudflared.exe'));
    candidates.push(path.join(localAppData, 'Programs', 'cloudflared', 'cloudflared.exe'));
  }
  candidates.push('/usr/local/bin/cloudflared', '/usr/bin/cloudflared', '/opt/homebrew/bin/cloudflared');

  const paths = (process.env.PATH ?? '').split(path.delimiter).filter(Boolean);
  for (const dir of paths) candidates.push(path.join(dir, exe));

  for (const candidate of candidates) {
    try {
      if (candidate && fs.existsSync(candidate)) return candidate;
    } catch {
      /* нет доступа — пропускаем */
    }
  }
  return null;
}

const cloudflared = args.tunnel ? findCloudflared() : null;
let tunnel = null;

if (cloudflared) {
  console.log(`  Найден cloudflared: ${cloudflared}`);
  console.log('  Публикуем сервер в интернете…\n');
  tunnel = spawn(cloudflared, ['tunnel', '--url', `http://127.0.0.1:${args.port}`, '--no-autoupdate'], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  // Настоящий адрес туннеля печатается в рамке; api.trycloudflare.com — это
  // служебный endpoint, его показывать нельзя.
  const TUNNEL_URL = /https:\/(?!api\.)[a-z0-9-]+\.trycloudflare\.com/;
  let announced = false;
  const onLine = (line) => {
    const text = String(line);
    const match = TUNNEL_URL.exec(text);
    if (match && !announced) {
      announced = true;
      console.log('');
      console.log('  ┌──────────────────────────────────────────────────────────────┐');
      console.log(`  │  ПУБЛИЧНЫЙ АДРЕС: ${match[0]}`);
      console.log('  │  Откройте его на телефоне — приложение и онлайн уже работают.  │');
      console.log('  │  «Онлайн» → «+ Создать комнату» → отправьте другу код.         │');
      console.log('  └──────────────────────────────────────────────────────────────┘');
      console.log('');
    }
  };
  tunnel.stdout.on('data', onLine);
  tunnel.stderr.on('data', onLine);
  tunnel.on('exit', (code) => {
    if (announced) {
      console.log(`\n  Туннель закрыт (код ${code}). Сервер продолжает работать локально.\n`);
      return;
    }
    console.log(`\n  Туннель не поднялся (код ${code}).`);
    console.log('  Самые частые причины:');
    console.log('   · нет доступа к api.trycloudflare.com (фаервол, корпоративная сеть, песочница);');
    console.log('   · cloudflared не смог выйти в интернет.');
    console.log('  Что делать: запустите туннель там, где интернет открыт — свой ПК');
    console.log('  (npm run online:tunnel) или notebook deploy/RevealAtlas-Online-Colab.ipynb');
    console.log('  из Google Диска: там cloudflared работает на стороне Google.');
    console.log('  А пока сервер доступен локально — адрес выше.\n');
  });
} else {
  console.log('  cloudflared не найден — сервер доступен только в локальной сети.');
  console.log('  Установите cloudflared (winget install Cloudflare.cloudflared / brew install cloudflared),');
  console.log('  тогда этот же скрипт сразу выдаст публичный адрес.');
  console.log('  Альтернатива без установок: notebook из папки deploy/ (Google Colab + Drive).\n');
}

function shutdown() {
  console.log('\nСохраняем комнаты и выходим…');
  online.stop();
  if (tunnel && !tunnel.killed) tunnel.kill();
  httpServer.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1200).unref();
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
void os.hostname();
