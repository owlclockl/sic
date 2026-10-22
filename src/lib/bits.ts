import { tileRect, type Grid } from './grid';

export interface Revealed {
  cols: number;
  rows: number;
  /** 1 — клетка открыта, 0 — скрыта. Индекс: row * cols + col. */
  bits: Uint8Array;
}

export function emptyRevealed(cols: number, rows: number): Revealed {
  return { cols, rows, bits: new Uint8Array(Math.max(0, cols * rows)) };
}

export function countRevealed(rev: Revealed): number {
  let n = 0;
  const bits = rev.bits;
  for (let i = 0; i < bits.length; i++) if (bits[i]) n++;
  return n;
}

export function isRevealed(rev: Revealed, col: number, row: number): boolean {
  if (col < 0 || row < 0 || col >= rev.cols || row >= rev.rows) return false;
  return rev.bits[row * rev.cols + col] === 1;
}

/**
 * Подгоняет маску прогресса под новые размеры сетки, сохраняя
 * уже открытые клетки в левом верхнем углу (по индексам).
 */
export function resizeRevealed(prev: Revealed, cols: number, rows: number): Revealed {
  if (prev.cols === cols && prev.rows === rows && prev.bits.length === cols * rows) return prev;
  const bits = new Uint8Array(Math.max(0, cols * rows));
  const copyCols = Math.min(cols, prev.cols);
  const copyRows = Math.min(rows, prev.rows);
  let changed = copyCols * copyRows > 0;
  for (let r = 0; r < copyRows; r++) {
    for (let c = 0; c < copyCols; c++) {
      const v = prev.bits[r * prev.cols + c];
      if (v) {
        bits[r * cols + c] = v;
      }
    }
  }
  if (!changed && prev.bits.length === bits.length) return prev;
  return { cols, rows, bits };
}

/**
 * Переносит маску на новую сетку по площади: открытая область остаётся
 * на том же месте карты. При укрупнении клеток открытая зона не теряется,
 * при измельчении — сохраняет всю раскрытую площадь.
 */
export function remapRevealed(prev: Revealed, prevGrid: Grid, nextGrid: Grid): Revealed {
  const same =
    prev.cols === nextGrid.cols &&
    prev.rows === nextGrid.rows &&
    prevGrid.cols === nextGrid.cols &&
    prevGrid.rows === nextGrid.rows &&
    prevGrid.tileW === nextGrid.tileW &&
    prevGrid.tileH === nextGrid.tileH &&
    prevGrid.originX === nextGrid.originX &&
    prevGrid.originY === nextGrid.originY;
  if (same) return prev;

  const bits = new Uint8Array(Math.max(0, nextGrid.cols * nextGrid.rows));
  const eps = 1e-6;
  for (let row = 0; row < prev.rows; row++) {
    for (let col = 0; col < prev.cols; col++) {
      if (!prev.bits[row * prev.cols + col]) continue;
      const rect = tileRect(prevGrid, col, row);
      const c0 = Math.max(0, Math.floor((rect.x - nextGrid.originX) / nextGrid.tileW));
      const c1 = Math.min(nextGrid.cols - 1, Math.floor((rect.x + rect.w - eps - nextGrid.originX) / nextGrid.tileW));
      const r0 = Math.max(0, Math.floor((rect.y - nextGrid.originY) / nextGrid.tileH));
      const r1 = Math.min(nextGrid.rows - 1, Math.floor((rect.y + rect.h - eps - nextGrid.originY) / nextGrid.tileH));
      for (let r = r0; r <= r1; r++) {
        const base = r * nextGrid.cols;
        for (let c = c0; c <= c1; c++) bits[base + c] = 1;
      }
    }
  }
  return { cols: nextGrid.cols, rows: nextGrid.rows, bits };
}

/** Считает открытые клетки в base64-маске (без раскодирования в Uint8Array). */
export function countBitsInEncoded(bits: string): number {
  let open = 0;
  for (let i = 0; i < bits.length; i++) {
    const v = B64_LOOKUP[bits[i]];
    if (v === undefined) continue;
    open += v.toString(2).split('1').length - 1;
  }
  return open;
}

/** Применяет список изменений [col, row, value] к маске. */
export function applyCells(rev: Revealed, cells: Array<[number, number, number]>): Revealed {
  let bits: Uint8Array | null = null;
  for (const [col, row, value] of cells) {
    if (!Number.isInteger(col) || !Number.isInteger(row)) continue;
    if (col < 0 || row < 0 || col >= rev.cols || row >= rev.rows) continue;
    const idx = row * rev.cols + col;
    const next = value ? 1 : 0;
    if (rev.bits[idx] === next) continue;
    if (!bits) bits = rev.bits.slice();
    bits[idx] = next;
  }
  return bits ? { cols: rev.cols, rows: rev.rows, bits } : rev;
}

/** То же, но для сохранённой (base64) маски проекта. */
export function applyCellsToEncoded(
  encoded: { cols: number; rows: number; bits: string },
  cells: Array<[number, number, number]>,
): { cols: number; rows: number; bits: string } {
  const bits = decodeBits(encoded.bits, encoded.cols * encoded.rows);
  let changed = false;
  for (const [col, row, value] of cells) {
    if (!Number.isInteger(col) || !Number.isInteger(row)) continue;
    if (col < 0 || row < 0 || col >= encoded.cols || row >= encoded.rows) continue;
    const idx = row * encoded.cols + col;
    const next = value ? 1 : 0;
    if (bits[idx] === next) continue;
    bits[idx] = next;
    changed = true;
  }
  return changed ? { cols: encoded.cols, rows: encoded.rows, bits: encodeBits(bits) } : encoded;
}

/** Заполняет сохранённую маску одним значением («открыть всё» / «скрыть всё»). */
export function fillEncoded(
  encoded: { cols: number; rows: number; bits: string },
  value: boolean,
): { cols: number; rows: number; bits: string } {
  const bits = new Uint8Array(Math.max(0, encoded.cols * encoded.rows));
  if (value) bits.fill(1);
  return { cols: encoded.cols, rows: encoded.rows, bits: encodeBits(bits) };
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

const B64_LOOKUP: Record<string, number> = (() => {
  const map: Record<string, number> = {};
  for (let i = 0; i < B64.length; i++) map[B64[i]] = i;
  return map;
})();

/** Компактное кодирование маски в base64 (1 бит на клетку). */
export function encodeBits(bits: Uint8Array): string {
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
  if (accBits > 0) {
    out += B64[(acc << (6 - accBits)) & 63];
  }
  return out;
}

export function decodeBits(str: string, length: number): Uint8Array {
  const bits = new Uint8Array(Math.max(0, length));
  if (!str) return bits;
  let acc = 0;
  let accBits = 0;
  let index = 0;
  for (let i = 0; i < str.length && index < bits.length; i++) {
    const v = B64_LOOKUP[str[i]];
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
