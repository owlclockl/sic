export type GridMode = 'tile' | 'columns';

export interface GridConfig {
  /** 'tile' — плитка задаётся размером в пикселях исходного изображения,
   *  'columns' — плитка задаётся количеством колонок по ширине мира. */
  mode: GridMode;
  tileSize: number;
  columns: number;
  offsetX: number;
  offsetY: number;
}

export interface Grid {
  cols: number;
  rows: number;
  /** Размер плитки в пикселях исходного изображения. */
  tileW: number;
  tileH: number;
  originX: number;
  originY: number;
  imageW: number;
  imageH: number;
}

export const DEFAULT_GRID: GridConfig = {
  mode: 'tile',
  tileSize: 64,
  columns: 24,
  offsetX: 0,
  offsetY: 0,
};

export const MIN_TILE = 8;
export const MAX_TILE = 512;
export const MIN_COLUMNS = 4;
export const MAX_COLUMNS = 200;
export const MAX_ROWS = 512;

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function safeNumber(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

/** Пересчитывает геометрию сетки для конкретного разрешения мира. */
export function computeGrid(imageW: number, imageH: number, cfg: GridConfig): Grid {
  const w = Math.max(1, imageW);
  const h = Math.max(1, imageH);

  let tileW: number;
  if (cfg.mode === 'columns') {
    const cols = clamp(Math.round(safeNumber(cfg.columns, 24)), MIN_COLUMNS, MAX_COLUMNS);
    tileW = w / cols;
  } else {
    tileW = clamp(Math.round(safeNumber(cfg.tileSize, 64)), MIN_TILE, MAX_TILE);
  }
  tileW = Math.max(1, tileW);
  const tileH = tileW;

  const originX = ((safeNumber(cfg.offsetX, 0) % tileW) + tileW) % tileW;
  const originY = ((safeNumber(cfg.offsetY, 0) % tileH) + tileH) % tileH;

  const cols = clamp(Math.ceil((w - originX) / tileW), 1, MAX_COLUMNS * 4);
  const rows = clamp(Math.ceil((h - originY) / tileH), 1, MAX_ROWS);

  return { cols, rows, tileW, tileH, originX, originY, imageW: w, imageH: h };
}

/** Прямоугольник плитки в координатах изображения (обрезан краем мира). */
export function tileRect(grid: Grid, col: number, row: number) {
  const x = grid.originX + col * grid.tileW;
  const y = grid.originY + row * grid.tileH;
  return {
    x,
    y,
    w: Math.min(grid.tileW, grid.imageW - x),
    h: Math.min(grid.tileH, grid.imageH - y),
  };
}

/** Плитка под точкой мира (в пикселях изображения) или null. */
export function tileAt(grid: Grid, x: number, y: number, clampToGrid = false): { col: number; row: number } | null {
  if (!clampToGrid && (x < 0 || y < 0 || x >= grid.imageW || y >= grid.imageH)) return null;
  const col = clamp(Math.floor((x - grid.originX) / grid.tileW), 0, grid.cols - 1);
  const row = clamp(Math.floor((y - grid.originY) / grid.tileH), 0, grid.rows - 1);
  return { col, row };
}

export function isInsideWorld(grid: Grid, x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < grid.imageW && y < grid.imageH;
}

export function gridSignature(grid: Grid): string {
  return `${grid.imageW}x${grid.imageH}:${grid.cols}x${grid.rows}:${grid.tileW}:${grid.originX}:${grid.originY}`;
}

export function describeTileSize(grid: Grid): string {
  const rounded = Math.round(grid.tileW * 10) / 10;
  return `${rounded} px`;
}
