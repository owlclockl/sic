/**
 * Сквозная проверка онлайн-режима: поднимает настоящий сервер на случайном
 * порту, соединяет двух «игроков» по WebSocket и проверяет, что мазки,
 * сетка, чат, курсоры и сохранение комнаты работают по-настоящему.
 *
 *   npm run test:online
 */
import http from 'node:http';
import zlib from 'node:zlib';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { attachOnlineServer } from '../server/online.mjs';

const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
const failures = [];
let checks = 0;

function check(label, condition, extra = '') {
  checks += 1;
  if (condition) {
    console.log(`  ✔ ${label}`);
  } else {
    console.log(`  ✘ ${label} ${extra}`);
    failures.push(label);
  }
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

/** Простая цветная PNG 4×4 — картинка мира для комнаты. */
function makePng() {
  const w = 4;
  const h = 4;
  const raw = Buffer.alloc((w * 4 + 1) * h);
  let p = 0;
  for (let y = 0; y < h; y++) {
    raw[p++] = 0;
    for (let x = 0; x < w; x++) {
      raw[p++] = 40 + x * 20;
      raw[p++] = 90;
      raw[p++] = 140;
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

/* ── крошечный WebSocket-клиент (только для теста) ── */

class TestSocket {
  constructor(url) {
    this.url = new URL(url);
    this.buffer = Buffer.alloc(0);
    this.fragments = [];
    this.messages = [];
    this.waiters = [];
    this.closed = false;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const key = Buffer.from(Math.random().toString(36).slice(2).repeat(3)).toString('base64').slice(0, 22);
      const req = http.request({
        host: this.url.hostname,
        port: Number(this.url.port),
        path: this.url.pathname,
        headers: {
          Connection: 'Upgrade',
          Upgrade: 'websocket',
          'Sec-WebSocket-Key': key,
          'Sec-WebSocket-Version': '13',
        },
      });
      req.on('upgrade', (res, socket) => {
        this.socket = socket;
        socket.on('data', (chunk) => this._onData(chunk));
        socket.on('close', () => {
          this.closed = true;
        });
        socket.on('error', () => {});
        resolve();
      });
      req.on('error', reject);
      req.end();
    });
  }

  _onData(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    for (;;) {
      if (this.buffer.length < 2) return;
      const b1 = this.buffer[1];
      let len = b1 & 0x7f;
      let offset = 2;
      if (len === 126) {
        if (this.buffer.length < 4) return;
        len = this.buffer.readUInt16BE(2);
        offset = 4;
      } else if (len === 127) {
        if (this.buffer.length < 10) return;
        len = Number(this.buffer.readBigUInt64BE(2));
        offset = 10;
      }
      if (this.buffer.length < offset + len) return;
      const payload = this.buffer.subarray(offset, offset + len);
      this.buffer = this.buffer.subarray(offset + len);
      const opcode = this.buffer.length >= 0 ? this._lastOpcode : 1;
      void opcode;
      // Сервер шлёт только целые текстовые кадры.
      const text = payload.toString('utf8');
      try {
        this.messages.push(JSON.parse(text));
      } catch {
        this.messages.push(text);
      }
      const waiter = this.waiters.shift();
      if (waiter) waiter();
    }
  }

  send(obj) {
    const payload = Buffer.from(JSON.stringify(obj), 'utf8');
    const mask = Buffer.from([1, 2, 3, 4]);
    let header;
    if (payload.length < 126) {
      header = Buffer.from([0x81, 0x80 | payload.length]);
    } else {
      header = Buffer.alloc(4);
      header[0] = 0x81;
      header[1] = 0x80 | 126;
      header.writeUInt16BE(payload.length, 2);
    }
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i++) masked[i] = payload[i] ^ mask[i & 3];
    this.socket.write(Buffer.concat([header, mask, masked]));
  }

  /** Ждёт сообщение, удовлетворяющее предикату. */
  waitFor(predicate, timeoutMs = 4000) {
    const found = this.messages.find(predicate);
    if (found) return Promise.resolve(found);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Таймаут ожидания сообщения')), timeoutMs);
      const poll = () => {
        const hit = this.messages.find(predicate);
        if (hit) {
          clearTimeout(timer);
          resolve(hit);
          return;
        }
        if (this.closed) {
          clearTimeout(timer);
          reject(new Error('Сокет закрыт'));
          return;
        }
        setTimeout(poll, 20);
      };
      poll();
    });
  }

  close() {
    try {
      this.socket?.destroy();
    } catch {
      /* ignore */
    }
  }
}

/* ── сценарий ── */

async function main() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-online-'));
  const png = makePng();
  const dataUrl = `data:image/png;base64,${png.toString('base64')}`;

  const httpServer = http.createServer();
  const online = attachOnlineServer(httpServer, { dataDir, staticRoot: null });
  await new Promise((resolve) => httpServer.listen(0, '127.0.0.1', resolve));
  const port = httpServer.address().port;
  const base = `http://127.0.0.1:${port}`;

  console.log(`\nОнлайн-сервер поднят на ${base}\n`);

  try {
    // 1. HTTP API
    const health = await fetch(`${base}/api/health`).then((r) => r.json());
    check('GET /api/health отвечает', health?.ok === true && health.service === 'reveal-atlas-online');

    const create = await fetch(`${base}/api/rooms`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: 'Тестовый мир',
        image: { dataUrl, name: 'test.png', width: 4, height: 4 },
        grid: { mode: 'tile', tileSize: 2, columns: 2, offsetX: 0, offsetY: 0 },
        revealed: { cols: 2, rows: 2, bits: '' },
      }),
    }).then((r) => r.json());
    check('POST /api/rooms создаёт комнату', create?.ok === true && /^[A-Z0-9]{6}$/.test(create.code ?? ''), JSON.stringify(create));
    const code = create.code;

    const imageRes = await fetch(`${base}/api/room/${code}/image`);
    const imageBytes = Buffer.from(await imageRes.arrayBuffer());
    check('GET /api/room/:code/image отдаёт картинку', imageRes.ok && imageBytes.equals(png));

    // 2. Два игрока
    const a = new TestSocket(`ws://127.0.0.1:${port}/ws`);
    const b = new TestSocket(`ws://127.0.0.1:${port}/ws`);
    await a.connect();
    await b.connect();

    a.send({ t: 'join', code, player: { id: 'player-a', name: 'Аня' } });
    const welcomeA = await a.waitFor((m) => m.t === 'welcome');
    check('игрок A получает welcome', welcomeA.you.host === true && welcomeA.room.code === code);
    check('в комнате одна картинка', welcomeA.room.image?.url === `/api/room/${code}/image?v=1`);
    check('сетка 2×2', welcomeA.room.cols === 2 && welcomeA.room.rows === 2);

    b.send({ t: 'join', code, player: { id: 'player-b', name: 'Боря' } });
    const welcomeB = await b.waitFor((m) => m.t === 'welcome');
    check('игрок B получает welcome', welcomeB.you.host === false && welcomeB.you.color !== welcomeA.you.color);

    const playersForA = await a.waitFor((m) => m.t === 'players' && m.players.length === 2);
    check('A видит двух игроков', playersForA.players.some((p) => p.id === 'player-b'));
    const joinSystem = await a.waitFor((m) => m.t === 'system');
    check('сообщение о входе игрока', /Боря/.test(joinSystem.text ?? ''));

    // 3. Мазок A → B
    a.send({ t: 'paint', cells: [[0, 0, 1]] });
    const paintForB = await b.waitFor((m) => m.t === 'paint');
    check('мазок A доходит до B', paintForB.cells?.[0]?.[0] === 0 && paintForB.cells?.[0]?.[1] === 0);
    check('автор мазка передан', paintForB.by?.name === 'Аня');

    // 4. Мазок B → A
    b.send({ t: 'paint', cells: [[1, 1, 1]] });
    const paintForA = await a.waitFor((m) => m.t === 'paint');
    check('мазок B доходит до A', paintForA.cells?.[0]?.[0] === 1 && paintForA.cells?.[0]?.[1] === 1);

    // 5. Курсор
    b.send({ t: 'cursor', col: 1, row: 0 });
    const cursorForA = await a.waitFor((m) => m.t === 'cursor');
    check('курсор B доходит до A', cursorForA.id === 'player-b' && cursorForA.col === 1 && cursorForA.row === 0);

    // 6. Чат
    a.send({ t: 'chat', text: 'привет из теста' });
    const chatForB = await b.waitFor((m) => m.t === 'chat');
    check('чат доходит до B', chatForB.message?.text === 'привет из теста' && chatForB.message?.name === 'Аня');

    // 7. Сетка
    b.send({ t: 'grid', config: { mode: 'tile', tileSize: 8, columns: 2, offsetX: 0, offsetY: 0 } });
    const gridForA = await a.waitFor((m) => m.t === 'grid');
    check('сетка синхронизируется', gridForA.config?.tileSize === 8);

    // 8. «Открыть всё»
    b.send({ t: 'fill', value: true });
    const fillForA = await a.waitFor((m) => m.t === 'fill');
    check('«открыть всё» доходит до A', fillForA.value === true && fillForA.cells === undefined);

    // 9. Список комнат
    a.send({ t: 'list' });
    const roomsForA = await a.waitFor((m) => m.t === 'rooms');
    check('лобби показывает комнату', roomsForA.rooms?.some((r) => r.code === code && r.players === 2));

    // 10. Выход игрока
    b.send({ t: 'leave' });
    const afterLeave = await a.waitFor((m) => m.t === 'players' && m.players.length === 1);
    check('после выхода остаётся один игрок', afterLeave.players.every((p) => p.id === 'player-a'));

    // 11. Состояние комнаты через HTTP
    const state = await fetch(`${base}/api/room/${code}/state`).then((r) => r.json());
    check('состояние комнаты отдаётся', state.room?.players?.length === 1 && state.room?.progress === 1);

    // 12. Переподключение того же игрока
    const a2 = new TestSocket(`ws://127.0.0.1:${port}/ws`);
    await a2.connect();
    a2.send({ t: 'join', code, player: { id: 'player-a', name: 'Аня' } });
    const welcomeAgain = await a2.waitFor((m) => m.t === 'welcome');
    check('переподключение сохраняет прогресс', welcomeAgain.room?.progress === 1);
    a2.close();

    a.close();

    // 13. Сохранение на диск
    await new Promise((r) => setTimeout(r, 300));
    const saved = JSON.parse(fs.readFileSync(path.join(dataDir, 'rooms', code, 'room.json'), 'utf8'));
    check('комната сохранена на диске', saved.code === code && saved.mask.length > 0);

    const store2 = attachOnlineServer(http.createServer(), { dataDir, staticRoot: null });
    const reloaded = store2.store.get(code);
    check('после перезапуска комната загружается', reloaded?.progress === 1 && reloaded?.name === 'Тестовый мир');
    store2.stop();
  } finally {
    online.stop();
    httpServer.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }

  console.log('');
  if (failures.length > 0) {
    console.log(`Провалено проверок: ${failures.length} из ${checks}`);
    for (const f of failures) console.log(`  · ${f}`);
    process.exit(1);
  }
  console.log(`Все проверки пройдены (${checks}).\n`);
}

main().catch((err) => {
  console.error('\nСамопроверка упала:', err);
  process.exit(1);
});
