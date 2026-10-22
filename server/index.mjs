#!/usr/bin/env node
/**
 * Автономный сервер Reveal Atlas: раздаёт собранное приложение из dist/
 * и поднимает настоящий онлайн-режим (WebSocket, комнаты, общий прогресс).
 *
 *   node server/index.mjs                        # порт 8080, данные в ./data/online
 *   node server/index.mjs --port 3000
 *   node server/index.mjs --host 0.0.0.0 --data /srv/atlas --dist ./dist
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { attachOnlineServer } from './online.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

function parseArgs(argv) {
  const options = {
    port: Number(process.env.PORT) || 8080,
    host: process.env.HOST || '0.0.0.0',
    data: null,
    dist: null,
  };
  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--port' || arg === '-p') options.port = Number(argv[++i]);
    else if (arg === '--host') options.host = argv[++i];
    else if (arg === '--data' || arg === '-d') options.data = argv[++i];
    else if (arg === '--dist') options.dist = argv[++i];
    else if (arg === '--help') {
      console.log(
        'Использование: node server/index.mjs [--port 8080] [--host 0.0.0.0] [--data ./data/online] [--dist ./dist]',
      );
      process.exit(0);
    }
  }
  return options;
}

const args = parseArgs(process.argv);
const distDir = path.resolve(args.dist ?? path.join(ROOT, 'dist'));
const dataDir = path.resolve(args.data ?? path.join(ROOT, 'data', 'online'));

if (!fs.existsSync(path.join(distDir, 'index.html'))) {
  console.error(`Не найдено приложение в ${distDir}.`);
  console.error('Сначала соберите проект: npm run build');
  process.exit(1);
}

fs.mkdirSync(dataDir, { recursive: true });

const httpServer = http.createServer();
httpServer.on('clientError', (_err, socket) => {
  if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
});

const online = attachOnlineServer(httpServer, { dataDir, staticRoot: distDir });

httpServer.listen(args.port, args.host, () => {
  const address = httpServer.address();
  const port = typeof address === 'object' && address ? address.port : args.port;
  const shownHost = args.host === '0.0.0.0' ? 'localhost' : args.host;
  console.log('');
  console.log('  Reveal Atlas — онлайн-сервер запущен');
  console.log(`  Приложение:   http://${shownHost}:${port}/`);
  console.log(`  Онлайн (WS):  ws://${shownHost}:${port}/ws`);
  console.log(`  Данные:       ${dataDir}`);
  console.log('');
  console.log('  Откройте приложение в двух окнах (или отправьте ссылку другу),');
  console.log('  создайте комнату в одном и подключитесь по коду в другом.');
  console.log('');
});

function shutdown(signal) {
  console.log(`\nПолучен ${signal}, сохраняем комнаты и выходим…`);
  online.stop();
  httpServer.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1500).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
