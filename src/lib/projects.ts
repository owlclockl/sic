import { DEFAULT_GRID, type GridConfig } from './grid';
import { decodeBits, emptyRevealed, encodeBits, type Revealed } from './bits';

export interface ProjectImage {
  /** Имя исходного файла или 'demo-world.jpg'. */
  name: string;
  width: number;
  height: number;
  /** data:URL — картинка живёт вместе с проектом в localStorage. */
  dataUrl: string;
}

export interface Project {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  image: ProjectImage;
  grid: GridConfig;
  revealed: { cols: number; rows: number; bits: string };
  brushSize: number;
}

export interface StoredState {
  version: 1;
  activeId: string | null;
  projects: Project[];
}

export const STORAGE_KEY = 'reveal-atlas/v1';

export function newId(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

export function createProject(name: string, image: ProjectImage, grid: GridConfig = DEFAULT_GRID): Project {
  const now = Date.now();
  return {
    id: newId(),
    name,
    createdAt: now,
    updatedAt: now,
    image,
    grid,
    revealed: { cols: 0, rows: 0, bits: '' },
    brushSize: 1,
  };
}

export function projectRevealed(project: Project, cols: number, rows: number): Revealed {
  if (!project.revealed.bits) return emptyRevealed(cols, rows);
  const bits = decodeBits(project.revealed.bits, cols * rows);
  if (project.revealed.cols !== cols || project.revealed.rows !== rows) {
    // Маска была снята при другой сетке — переносим её в левый верхний угол.
    const prevCols = project.revealed.cols;
    const next = new Uint8Array(cols * rows);
    for (let r = 0; r < Math.min(rows, project.revealed.rows); r++) {
      for (let c = 0; c < Math.min(cols, prevCols); c++) {
        const v = bits[r * prevCols + c];
        if (v) next[r * cols + c] = v;
      }
    }
    return { cols, rows, bits: next };
  }
  return { cols, rows, bits };
}

export function serializeRevealed(cols: number, rows: number, bits: Uint8Array) {
  return { cols, rows, bits: encodeBits(bits) };
}

interface RawProject {
  id?: unknown;
  name?: unknown;
  createdAt?: unknown;
  updatedAt?: unknown;
  image?: { name?: unknown; width?: unknown; height?: unknown; dataUrl?: unknown };
  grid?: Partial<GridConfig>;
  revealed?: { cols?: unknown; rows?: unknown; bits?: unknown };
  brushSize?: unknown;
}

/** Приводит проект из localStorage/JSON к актуальной схеме. */
export function normalizeProject(raw: unknown): Project | null {
  const p = raw as RawProject;
  if (!p || typeof p !== 'object') return null;
  const image = p.image;
  if (!image || typeof image.dataUrl !== 'string' || !image.dataUrl.startsWith('data:image/')) return null;
  const width = Math.round(Number(image.width));
  const height = Math.round(Number(image.height));
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;

  const rawGrid = p.grid ?? {};
  const grid: GridConfig = {
    mode: rawGrid.mode === 'columns' ? 'columns' : 'tile',
    tileSize: Number.isFinite(Number(rawGrid.tileSize)) ? Number(rawGrid.tileSize) : DEFAULT_GRID.tileSize,
    columns: Number.isFinite(Number(rawGrid.columns)) ? Number(rawGrid.columns) : DEFAULT_GRID.columns,
    offsetX: Number.isFinite(Number(rawGrid.offsetX)) ? Number(rawGrid.offsetX) : 0,
    offsetY: Number.isFinite(Number(rawGrid.offsetY)) ? Number(rawGrid.offsetY) : 0,
  };

  const rawRevealed = p.revealed ?? {};
  const cols = Math.max(0, Math.round(Number(rawRevealed.cols) || 0));
  const rows = Math.max(0, Math.round(Number(rawRevealed.rows) || 0));
  const bits = typeof rawRevealed.bits === 'string' ? rawRevealed.bits : '';

  return {
    id: typeof p.id === 'string' && p.id ? p.id : newId(),
    name: typeof p.name === 'string' && p.name.trim() ? p.name.trim().slice(0, 60) : 'Мир',
    createdAt: Number(p.createdAt) || Date.now(),
    updatedAt: Number(p.updatedAt) || Date.now(),
    image: {
      name: typeof image.name === 'string' ? image.name : 'image',
      width,
      height,
      dataUrl: image.dataUrl,
    },
    grid,
    revealed: { cols, rows, bits },
    brushSize: Number.isFinite(Number(p.brushSize)) ? Math.min(9, Math.max(1, Math.round(Number(p.brushSize)))) : 1,
  };
}

export function loadState(): StoredState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { version?: unknown; activeId?: unknown; projects?: unknown[] };
    if (!parsed || !Array.isArray(parsed.projects)) return null;
    const projects = parsed.projects.map(normalizeProject).filter((p): p is Project => p !== null);
    if (projects.length === 0) return null;
    const activeId =
      typeof parsed.activeId === 'string' && projects.some((p) => p.id === parsed.activeId)
        ? parsed.activeId
        : projects[0].id;
    return { version: 1, activeId, projects };
  } catch {
    return null;
  }
}

export function saveState(state: StoredState): 'ok' | 'quota' | 'error' {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    return 'ok';
  } catch (err) {
    const quota =
      err instanceof DOMException &&
      (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED');
    return quota ? 'quota' : 'error';
  }
}

export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function removeProject(state: StoredState, id: string): StoredState {
  const projects = state.projects.filter((p) => p.id !== id);
  const activeId = state.activeId === id ? (projects[0]?.id ?? null) : state.activeId;
  return { ...state, projects, activeId };
}
