/**
 * Проверка настоящего клиента онлайн-режима: компилируем src/lib/online.ts
 * через esbuild и запускаем его в Node против живого сервера. Так тестируется
 * именно тот код, который работает в браузере: переподключение, буфер мазков,
 * протокол, создание комнат.
 *
 *   npm run test:online:client
 */
import http from 'node:http';
import zlib from 'node:zlib';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { build } from 'esbuild';
import { attachOnlineServer } from '../server/online.mjs';

const failures = [];
let checks = 0;

function check(label, condition, extra = '') {
  checks += 1;
  if (condition) console.log(`  ✔ ${label}`);
  else {
    console.log(`  ✘ ${label} ${extra}`);
    failures.push(label);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii');
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crc]);
}

function makePng() {
  const w = 6;
  const h = 4;
  const raw = Buffer.alloc((w * 4 + 1) * h);
  let p = 0;
  for (let y = 0; y < h; y++) {
    raw[p++] = 0;
    for (let x = 0; x < w; x++) {
      raw[p++] = 30 + x * 10;
      raw[p++] = 120;
      raw[p++] = 90;
      raw[p++] = 255;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

// localStorage нет в Node — подставляем простую заглушку до импорта клиента.
const memoryStorage = new Map();
globalThis.localStorage = {
  getItem: (key) => (memoryStorage.has(key) ? memoryStorage.get(key) : null),
  setItem: (key, value) => memoryStorage.set(key, String(value)),
  removeItem: (key) => memoryStorage.delete(key),
  clear: () => memoryStorage.clear(),
  key: (index) => [...memoryStorage.keys()][index] ?? null,
  get length() {
    return memoryStorage.size;
  },
};

const compiled = await build({
  entryPoints: [path.resolve('src/lib/online.ts')],
  bundle: true,
  format: 'esm',
  platform: 'browser',
  write: false,
  target: 'es2020',
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`;
const onlineModule = await import(moduleUrl);
const { OnlineClient } = onlineModule;

async function main() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-client-'));
  const png = makePng();
  const dataUrl = `data:image/png;base64,${png.toString('base64')}`;

  const server = http.createServer();
  const online = attachOnlineServer(server, { dataDir, staticRoot: null });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;

  console.log(`\nКлиент онлайн-режима против сервера ${base}\n`);

  const eventsA = [];
  const eventsB = [];
  const clientA = new OnlineClient({
    serverUrl: base,
    identity: { id: 'client-a', name: 'Аня' },
    onEvent: (event) => eventsA.push(event),
  });
  const clientB = new OnlineClient({
    serverUrl: base,
    identity: { id: 'client-b', name: 'Боря' },
    onEvent: (event) => eventsB.push(event),
  });

  const waitFor = async (list, predicate, timeoutMs = 5000) => {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const found = list.find(predicate);
      if (found) return found;
      if (Date.now() > deadline) throw new Error('Таймаут ожидания события');
      await sleep(25);
    }
  };

  try {
    // 1. Создание комнаты настоящим клиентом
    const { code, imageUrl } = await clientA.createRoom({
      name: 'Клиентский мир',
      image: { dataUrl, name: 'client.png', width: 6, height: 4 },
      grid: { mode: 'tile', tileSize: 8, columns: 3, offsetX: 0, offsetY: 0 },
      revealed: { cols: 3, rows: 2, bits: '' },
    });
    check('createRoom возвращает код', /^[A-Z0-9]{6}$/.test(code), code);
    check('createRoom возвращает адрес картинки', imageUrl.includes(code), imageUrl);

    // 2. Подключение и приглашение второго игрока
    clientA.joinRoom(code);
    await waitFor(eventsA, (e) => e.type === 'welcome');
    check('клиент A получает welcome', eventsA.some((e) => e.type === 'welcome' && e.you.host === true));
    check('статус становится online', clientA.getStatus() === 'online');

    clientB.joinRoom(code);
    await waitFor(eventsB, (e) => e.type === 'welcome');
    check('клиент B получает welcome', eventsB.some((e) => e.type === 'welcome'));
    check('у A появляется список игроков', eventsA.some((e) => e.type === 'players' && e.players.length === 2));

    // 3. Мазок A → B
    clientA.sendPaint([[0, 0, 1], [1, 0, 1]]);
    const paintAtB = await waitFor(eventsB, (e) => e.type === 'paint');
    check('мазок A доходит до клиента B', paintAtB.cells.length === 2 && paintAtB.cells[0][2] === 1);

    // 4. Курсор, чат, сетка, «открыть всё»
    clientA.sendCursor(2, 1);
    const cursorAtB = await waitFor(eventsB, (e) => e.type === 'cursor');
    check('курсор доходит до B', cursorAtB.col === 2 && cursorAtB.row === 1);

    clientB.sendChat('привет');
    const chatAtA = await waitFor(eventsA, (e) => e.type === 'chat');
    check('чат доходит до A', chatAtA.message.text === 'привет');

    clientB.sendGrid({ mode: 'tile', tileSize: 16, columns: 3, offsetX: 0, offsetY: 0 });
    const gridAtA = await waitFor(eventsA, (e) => e.type === 'grid');
    check('сетка доходит до A', gridAtA.config.tileSize === 16);

    clientA.sendFill(true);
    const fillAtB = await waitFor(eventsB, (e) => e.type === 'fill');
    check('«открыть всё» доходит до B', fillAtB.value === true);

    // 5. Лобби
    const rooms = await clientA.listRooms();
    check('listRooms показывает комнату', rooms.some((room) => room.code === code && room.players === 2));

    // 6. Переподключение после обрыва: гасим сервер и поднимаем его заново
    // на том же порту с теми же данными.
    online.stop();
    server.close();
    server.closeAllConnections?.();
    await sleep(200);
    check('статус уходит в переподключение', clientA.getStatus() === 'reconnecting', clientA.getStatus());

    const server2 = http.createServer();
    const online2 = attachOnlineServer(server2, { dataDir, staticRoot: null });
    await new Promise((resolve) => server2.listen(port, '127.0.0.1', resolve));

    const rewelcome = await waitFor(eventsA, (e) => e.type === 'welcome' && e.room.progress === 1, 12000);
    check('после обрыва клиент сам переподключается', rewelcome.room.code === code);
    check('прогресс комнаты сохранился', rewelcome.room.progress === 1);
    check('состояние снова online', clientA.getStatus() === 'online');

    // 7. Буфер офлайн-мазков: рисуем без связи и проверяем, что они уйдут
    server2.close();
    server2.closeAllConnections?.();
    await sleep(150);
    clientA.sendPaint([[2, 1, 1]]);
    clientA.sendFill(false);
    const server3 = http.createServer();
    const online3 = attachOnlineServer(server3, { dataDir, staticRoot: null });
    await new Promise((resolve) => server3.listen(port, '127.0.0.1', resolve));
    const afterBuffer = await waitFor(eventsA, (e) => e.type === 'welcome', 12000);
    await sleep(500);
    check('буфер офлайн-мазков доезжает до сервера', afterBuffer.room.progress < 1, String(afterBuffer.room.progress));

    // 8. Выход из комнаты
    clientA.leaveRoom();
    check('после выхода статус idle', clientA.getStatus() === 'idle');
    check('комната осталась на сервере', Boolean(online3.store.get(code)));

    online3.stop();
    server3.close();
  } finally {
    clientA.disconnect();
    clientB.disconnect();
    try {
      online.stop();
    } catch {
      /* уже остановлен */
    }
    try {
      server.close();
    } catch {
      /* ignore */
    }
    fs.rmSync(dataDir, { recursive: true, force: true });
  }

  console.log('');
  if (failures.length > 0) {
    console.log(`Провалено проверок: ${failures.length} из ${checks}`);
    for (const f of failures) console.log(`  · ${f}`);
    process.exit(1);
  }
  console.log(`Все проверки клиента пройдены (${checks}).\n`);
}

main().catch((err) => {
  console.error('\nПроверка клиента упала:', err);
  process.exit(1);
});
