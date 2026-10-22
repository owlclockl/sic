/**
 * Комнаты онлайн-режима: общий мир, маска открытых клеток, игроки, чат.
 * Комната живёт в памяти, а также сохраняется на диск, чтобы прогресс
 * не терялся при перезапуске сервера.
 */
import fs from 'node:fs';
import path from 'node:path';

export const ROOM_VERSION = 1;
export const MAX_PLAYERS = 24;
export const MAX_ROOMS = 64;
export const MAX_PAINT_CELLS = 4096;
export const MAX_CHAT_MESSAGES = 40;
export const CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';

const PLAYER_COLORS = [
  '#7ee0c3',
  '#ffd479',
  '#8ab4ff',
  '#ff9ecb',
  '#b6ff8a',
  '#c9a0ff',
  '#ff9f68',
  '#7dd3fc',
];

const MIME_EXT = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'image/webp': 'webp',
  'image/avif': 'avif',
  'image/gif': 'gif',
  'image/bmp': 'bmp',
};

/* ── маска клеток: base64, 1 бит на клетку (как в src/lib/bits.ts) ── */

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const B64_LOOKUP = (() => {
  const map = new Map();
  for (let i = 0; i < B64.length; i++) map.set(B64[i], i);
  return map;
})();

export function encodeMask(bits) {
  let out = '';
  let acc = 0;
  let accBits = 0;
  for (let i = 0; i < bits.length; i++) {
    acc = (acc << 1) | (bits[i] ? 1 : 0);
    accBits++;
    if (accBits === 6) {
      out += B64[acc];
      acc = 0;
      accBits = 0;
    }
  }
  if (accBits > 0) out += B64[(acc << (6 - accBits)) & 63];
  return out;
}

export function decodeMask(str, length) {
  const bits = Buffer.alloc(Math.max(0, length));
  if (!str) return bits;
  let acc = 0;
  let accBits = 0;
  let index = 0;
  for (let i = 0; i < str.length && index < bits.length; i++) {
    const v = B64_LOOKUP.get(str[i]);
    if (v === undefined) continue;
    acc = (acc << 6) | v;
    accBits += 6;
    while (accBits >= 1 && index < bits.length) {
      accBits--;
      bits[index++] = (acc >> accBits) & 1;
    }
    acc &= (1 << accBits) - 1;
  }
  return bits;
}

/* ── утилиты ── */

function randomCode(existing) {
  for (let attempt = 0; attempt < 40; attempt++) {
    let code = '';
    for (let i = 0; i < 6; i++) {
      code += CODE_ALPHABET[Math.floor(Math.random() * CODE_ALPHABET.length)];
    }
    if (!existing.has(code)) return code;
  }
  return `${Date.now().toString(36).toUpperCase().slice(-4)}${Math.floor(Math.random() * 90 + 10)}`;
}

export function normalizeCode(raw) {
  const code = String(raw ?? '')
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '');
  if (code.length < 4 || code.length > 12) return null;
  return code;
}

function sanitizeName(raw, fallback) {
  const name = String(raw ?? '').replace(/[\u0000-\u001f<>]/g, '').trim();
  return name.slice(0, 60) || fallback;
}

function intInRange(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

export function normalizeGridConfig(raw) {
  const mode = raw?.mode === 'columns' ? 'columns' : 'tile';
  return {
    mode,
    tileSize: intInRange(raw?.tileSize, 4, 4096, 64),
    columns: intInRange(raw?.columns, 2, 2000, 24),
    offsetX: intInRange(raw?.offsetX, -4096, 4096, 0),
    offsetY: intInRange(raw?.offsetY, -4096, 4096, 0),
  };
}

function imageMetaFromDataUrl(dataUrl, name) {
  const match = /^data:([\w.+-]+\/[\w.+-]+);base64,(.*)$/s.exec(String(dataUrl ?? ''));
  if (!match) return null;
  const mime = match[1].toLowerCase();
  const ext = MIME_EXT[mime];
  if (!ext) return null;
  const bytes = Buffer.from(match[2], 'base64');
  if (bytes.length === 0 || bytes.length > 48 * 1024 * 1024) return null;
  return { mime, ext, bytes, name: sanitizeName(name, 'world') };
}

/* ── комната ── */

export class Room {
  constructor(code, { name, image, grid, revealed }) {
    this.code = code;
    this.name = name;
    this.grid = grid;
    this.cols = revealed.cols;
    this.rows = revealed.rows;
    this.mask = decodeMask(revealed.bits, revealed.cols * revealed.rows);
    this.image = image;
    this.createdAt = Date.now();
    this.updatedAt = this.createdAt;
    this.players = new Map();
    this.chat = [];
    this.hostId = null;
    this.saveTimer = null;
    this.lastEmptyAt = Date.now();
  }

  get total() {
    return this.cols * this.rows;
  }

  get progress() {
    if (!this.total) return 0;
    let open = 0;
    const mask = this.mask;
    for (let i = 0; i < mask.length; i++) if (mask[i]) open++;
    return Math.min(1, open / this.total);
  }

  playerList() {
    return [...this.players.values()].map((p) => ({
      id: p.id,
      name: p.name,
      color: p.color,
      cursor: p.cursor ?? null,
      host: p.id === this.hostId,
    }));
  }

  summary() {
    return {
      code: this.code,
      name: this.name,
      players: this.players.size,
      progress: this.progress,
      cols: this.cols,
      rows: this.rows,
      createdAt: this.createdAt,
      hasImage: Boolean(this.image),
    };
  }

  /** Полное состояние комнаты для нового игрока. */
  state() {
    return {
      code: this.code,
      name: this.name,
      cols: this.cols,
      rows: this.rows,
      grid: this.grid,
      image: this.image
        ? {
            url: `/api/room/${this.code}/image?v=${this.image.version}`,
            version: this.image.version,
            name: this.image.name,
            width: this.image.width,
            height: this.image.height,
            mime: this.image.mime,
          }
        : null,
      revealed: { cols: this.cols, rows: this.rows, bits: encodeMask(this.mask) },
      players: this.playerList(),
      hostId: this.hostId,
      progress: this.progress,
      createdAt: this.createdAt,
      chat: this.chat,
    };
  }

  /** Применяет мазок. Возвращает реально изменённые клетки. */
  applyCells(cells) {
    const changed = [];
    const cols = this.cols;
    const rows = this.rows;
    const mask = this.mask;
    for (const cell of cells) {
      if (!Array.isArray(cell) || cell.length < 3) continue;
      const col = Number(cell[0]);
      const row = Number(cell[1]);
      const value = Number(cell[2]) ? 1 : 0;
      if (!Number.isInteger(col) || !Number.isInteger(row)) continue;
      if (col < 0 || row < 0 || col >= cols || row >= rows) continue;
      const idx = row * cols + col;
      if (mask[idx] === value) continue;
      mask[idx] = value;
      changed.push([col, row, value]);
      if (changed.length >= MAX_PAINT_CELLS) break;
    }
    if (changed.length > 0) this.updatedAt = Date.now();
    return changed;
  }

  applyFill(value) {
    const next = value ? 1 : 0;
    const changed = [];
    const mask = this.mask;
    for (let i = 0; i < mask.length; i++) {
      if (mask[i] === next) continue;
      mask[i] = next;
      changed.push([i % this.cols, Math.floor(i / this.cols), next]);
      if (changed.length >= MAX_PAINT_CELLS) break;
    }
    if (changed.length > 0) this.updatedAt = Date.now();
    return changed;
  }
}

/* ── хранилище комнат ── */

export class RoomStore {
  constructor({ dataDir, idleUnloadMs = 10 * 60 * 1000 } = {}) {
    this.dataDir = dataDir;
    this.roomsDir = path.join(dataDir, 'rooms');
    this.idleUnloadMs = idleUnloadMs;
    this.rooms = new Map();
    fs.mkdirSync(this.roomsDir, { recursive: true });
    this.loadFromDisk();
  }

  roomDir(code) {
    return path.join(this.roomsDir, code);
  }

  metaPath(code) {
    return path.join(this.roomDir(code), 'room.json');
  }

  loadFromDisk() {
    let entries = [];
    try {
      entries = fs.readdirSync(this.roomsDir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      try {
        const raw = fs.readFileSync(path.join(this.roomsDir, entry.name, 'room.json'), 'utf8');
        const parsed = JSON.parse(raw);
        const room = this._fromDisk(parsed);
        if (room) this.rooms.set(room.code, room);
      } catch {
        /* повреждённая комната — пропускаем */
      }
    }
  }

  _fromDisk(parsed) {
    if (!parsed || parsed.version !== ROOM_VERSION) return null;
    const code = normalizeCode(parsed.code);
    if (!code) return null;
    const cols = intInRange(parsed.cols, 1, 20000, 0);
    const rows = intInRange(parsed.rows, 1, 20000, 0);
    if (!cols || !rows) return null;
    const room = new Room(code, {
      name: sanitizeName(parsed.name, 'Мир'),
      grid: normalizeGridConfig(parsed.grid),
      revealed: { cols, rows, bits: typeof parsed.mask === 'string' ? parsed.mask : '' },
    });
    room.createdAt = Number(parsed.createdAt) || Date.now();
    room.updatedAt = Number(parsed.updatedAt) || room.createdAt;
    room.lastEmptyAt = Date.now();
    const image = parsed.image;
    if (image && image.version) {
      const ext = String(image.mime ?? '').replace(/^image\//, '');
      const file = path.join(this.roomDir(code), `image-${Number(image.version)}.${ext}`);
      if (fs.existsSync(file)) {
        room.image = {
          version: Number(image.version),
          name: sanitizeName(image.name, 'world'),
          mime: `image/${ext === 'jpg' ? 'jpeg' : ext}`,
          width: intInRange(image.width, 1, 65536, 1),
          height: intInRange(image.height, 1, 65536, 1),
          file,
        };
      }
    }
    return room;
  }

  get(code) {
    return this.rooms.get(normalizeCode(code) ?? '') ?? null;
  }

  list() {
    return [...this.rooms.values()]
      .map((room) => room.summary())
      .sort((a, b) => b.players - a.players || b.updatedAt - a.updatedAt)
      .slice(0, 40);
  }

  create({ name, image, grid, revealed }) {
    if (this.rooms.size >= MAX_ROOMS) {
      const victim = [...this.rooms.values()]
        .filter((room) => room.players.size === 0)
        .sort((a, b) => a.updatedAt - b.updatedAt)[0];
      if (!victim) return { error: 'На сервере слишком много комнат. Попробуйте позже.' };
      this.persist(victim);
      this.rooms.delete(victim.code);
    }
    const cols = intInRange(revealed?.cols, 1, 20000, 0);
    const rows = intInRange(revealed?.rows, 1, 20000, 0);
    if (!cols || !rows) return { error: 'Некорректный размер сетки мира.' };

    const imageMeta = imageMetaFromDataUrl(image?.dataUrl, image?.name);
    if (!imageMeta) return { error: 'Не удалось прочитать картинку мира.' };

    const code = randomCode(this.rooms);
    const room = new Room(code, {
      name: sanitizeName(name, 'Мир'),
      grid: normalizeGridConfig(grid),
      revealed: { cols, rows, bits: typeof revealed?.bits === 'string' ? revealed.bits : '' },
    });
    room.image = {
      version: 1,
      name: imageMeta.name,
      mime: imageMeta.mime,
      width: intInRange(image?.width, 1, 65536, 1),
      height: intInRange(image?.height, 1, 65536, 1),
      file: null,
      bytes: imageMeta.bytes,
    };
    this.rooms.set(code, room);
    this._writeImage(room);
    this.persist(room);
    // Игроки подключаются отдельно, через WebSocket: комната создаётся пустой.
    return { room };
  }

  join(code, player) {
    const room = this.get(code);
    if (!room) return { error: 'Комната не найдена. Проверьте код.' };
    if (room.players.size >= MAX_PLAYERS) return { error: 'В комнате уже максимально игроков.' };
    const id = String(player?.id ?? '').slice(0, 40) || `p${Math.random().toString(36).slice(2, 10)}`;
    const name = sanitizeName(player?.name, 'Игрок');
    const existing = room.players.get(id);
    if (existing) {
      existing.name = name;
      return { room, player: existing };
    }
    const usedColors = new Set([...room.players.values()].map((p) => p.color));
    const color =
      PLAYER_COLORS.find((c) => !usedColors.has(c)) ?? PLAYER_COLORS[room.players.size % PLAYER_COLORS.length];
    const entry = { id, name, color, cursor: null, joinedAt: Date.now() };
    room.players.set(id, entry);
    if (!room.hostId) room.hostId = id;
    return { room, player: entry };
  }

  leave(room, playerId) {
    if (!room) return;
    const player = room.players.get(playerId);
    if (!player) return;
    room.players.delete(playerId);
    if (room.hostId === playerId) {
      room.hostId = room.players.values().next().value?.id ?? null;
    }
    if (room.players.size === 0) room.lastEmptyAt = Date.now();
    this.persist(room);
  }

  setGrid(room, config) {
    const next = normalizeGridConfig(config);
    const changed = JSON.stringify(next) !== JSON.stringify(room.grid);
    if (!changed) return false;
    room.grid = next;
    room.updatedAt = Date.now();
    this.schedulePersist(room);
    return true;
  }

  addChat(room, player, text) {
    const clean = String(text ?? '')
      .replace(/[\u0000-\u001f]/g, ' ')
      .trim()
      .slice(0, 300);
    if (!clean) return null;
    const message = {
      id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      name: player?.name ?? 'Игрок',
      color: player?.color ?? '#7ee0c3',
      text: clean,
      ts: Date.now(),
    };
    room.chat.push(message);
    if (room.chat.length > MAX_CHAT_MESSAGES) room.chat.splice(0, room.chat.length - MAX_CHAT_MESSAGES);
    return message;
  }

  /** Заменяет картинку комнаты. Возвращает false при ошибке. */
  setImage(room, image) {
    const meta = imageMetaFromDataUrl(image?.dataUrl, image?.name);
    if (!meta) return false;
    room.image = {
      version: (room.image?.version ?? 0) + 1,
      name: meta.name,
      mime: meta.mime,
      width: intInRange(image?.width, 1, 65536, room.image?.width ?? 1),
      height: intInRange(image?.height, 1, 65536, room.image?.height ?? 1),
      file: null,
      bytes: meta.bytes,
    };
    this._writeImage(room);
    room.updatedAt = Date.now();
    this.persist(room);
    return true;
  }

  _writeImage(room) {
    if (!room.image?.bytes) return;
    const dir = this.roomDir(room.code);
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `image-${room.image.version}.${room.image.ext}`);
    fs.writeFileSync(file, room.image.bytes);
    room.image.file = file;
    room.image.bytes = null;
  }

  imageFile(room) {
    if (!room.image) return null;
    if (room.image.file && fs.existsSync(room.image.file)) return room.image.file;
    const ext = room.image.mime.replace(/^image\//, '');
    const candidate = path.join(this.roomDir(room.code), `image-${room.image.version}.${ext}`);
    return fs.existsSync(candidate) ? candidate : null;
  }

  schedulePersist(room, delayMs = 1500) {
    if (room.saveTimer) return;
    room.saveTimer = setTimeout(() => {
      room.saveTimer = null;
      this.persist(room);
    }, delayMs);
    if (room.saveTimer.unref) room.saveTimer.unref();
  }

  persist(room) {
    try {
      const dir = this.roomDir(room.code);
      fs.mkdirSync(dir, { recursive: true });
      const payload = {
        version: ROOM_VERSION,
        code: room.code,
        name: room.name,
        grid: room.grid,
        cols: room.cols,
        rows: room.rows,
        mask: encodeMask(room.mask),
        image: room.image
          ? {
              version: room.image.version,
              name: room.image.name,
              mime: room.image.mime,
              width: room.image.width,
              height: room.image.height,
            }
          : null,
        createdAt: room.createdAt,
        updatedAt: room.updatedAt,
      };
      const tmp = path.join(dir, 'room.json.tmp');
      fs.writeFileSync(tmp, JSON.stringify(payload));
      fs.renameSync(tmp, path.join(dir, 'room.json'));
    } catch {
      /* диск недоступен — комната продолжает жить в памяти */
    }
  }

  /** Выгружает комнаты, в которых давно никого не было. */
  sweepIdle() {
    const now = Date.now();
    let unloaded = 0;
    for (const room of [...this.rooms.values()]) {
      if (room.players.size > 0) continue;
      if (now - room.lastEmptyAt < this.idleUnloadMs) continue;
      this.persist(room);
      this.rooms.delete(room.code);
      unloaded++;
    }
    return unloaded;
  }

  flushAll() {
    for (const room of this.rooms.values()) {
      if (room.saveTimer) {
        clearTimeout(room.saveTimer);
        room.saveTimer = null;
      }
      this.persist(room);
    }
  }
}
