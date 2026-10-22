export type WorldBiome = 'temperate' | 'desert' | 'frozen';

export interface ProceduralWorld {
  seed: number;
  biome: WorldBiome;
}

type Color = [number, number, number];

const PALETTES: Record<WorldBiome, { water: Color; sand: Color; grass: Color; forest: Color; rock: Color; snow: Color }> = {
  temperate: { water: [35, 102, 151], sand: [208, 190, 126], grass: [102, 164, 83], forest: [49, 120, 69], rock: [123, 119, 112], snow: [235, 240, 233] },
  desert: { water: [43, 119, 153], sand: [224, 184, 105], grass: [173, 161, 82], forest: [120, 138, 70], rock: [151, 117, 86], snow: [237, 222, 184] },
  frozen: { water: [75, 143, 177], sand: [191, 211, 207], grass: [139, 179, 151], forest: [60, 119, 111], rock: [127, 145, 146], snow: [237, 246, 247] },
};

function hash(x: number, y: number, seed: number): number {
  // Mix both halves of each safe integer so the terrain does not repeat every 2³² tiles.
  const xi = Math.trunc(x); const yi = Math.trunc(y);
  let h = (seed | 0)
    ^ Math.imul(xi >>> 0, 0x45d9f3b)
    ^ Math.imul(yi >>> 0, 0x27d4eb2d)
    ^ Math.imul(Math.trunc(xi / 4294967296), 0x165667b1)
    ^ Math.imul(Math.trunc(yi / 4294967296), 0x1b873593);
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function smooth(t: number): number { return t * t * (3 - 2 * t); }
function noise(x: number, y: number, seed: number): number {
  const ix = Math.floor(x); const iy = Math.floor(y);
  const fx = smooth(x - ix); const fy = smooth(y - iy);
  const a = hash(ix, iy, seed); const b = hash(ix + 1, iy, seed);
  const c = hash(ix, iy + 1, seed); const d = hash(ix + 1, iy + 1, seed);
  const top = a + (b - a) * fx; const bottom = c + (d - c) * fx;
  return top + (bottom - top) * fy;
}

function fbm(x: number, y: number, seed: number): number {
  let sum = 0; let amp = 0.55; let norm = 0;
  for (let octave = 0; octave < 4; octave++) {
    sum += noise(x, y, seed + octave * 7919) * amp;
    norm += amp; x *= 2; y *= 2; amp *= 0.5;
  }
  return sum / norm;
}

type Terrain = { color: Color; kind: 'water' | 'land' | 'mountain' };
const terrainCache = new Map<string, Terrain>();
function rememberTerrain(key: string, terrain: Terrain): Terrain {
  if (terrainCache.size > 30_000) terrainCache.clear();
  terrainCache.set(key, terrain);
  return terrain;
}

function terrainAt(x: number, y: number, world: ProceduralWorld): Terrain {
  const key = `${world.seed | 0}:${world.biome}:${x}:${y}`;
  const cached = terrainCache.get(key);
  if (cached) return cached;
  const seed = world.seed | 0;
  const elevation = fbm(x / 115, y / 115, seed) * 0.72 + fbm(x / 39 + 8.2, y / 39 - 2.4, seed + 83) * 0.28;
  const moisture = fbm(x / 88 - 21, y / 88 + 12, seed + 290);
  const palette = PALETTES[world.biome];
  if (elevation < 0.39) return rememberTerrain(key, { color: palette.water, kind: 'water' });
  if (elevation < 0.425) return rememberTerrain(key, { color: palette.sand, kind: 'land' });
  if (elevation > 0.77) return rememberTerrain(key, { color: palette.snow, kind: 'mountain' });
  if (elevation > 0.68) return rememberTerrain(key, { color: palette.rock, kind: 'mountain' });
  if (moisture > 0.59 && elevation < 0.65) return rememberTerrain(key, { color: palette.forest, kind: 'land' });
  if (world.biome === 'desert' && moisture < 0.58) return rememberTerrain(key, { color: palette.sand, kind: 'land' });
  if (world.biome === 'frozen' && elevation < 0.55) return rememberTerrain(key, { color: palette.snow, kind: 'land' });
  return rememberTerrain(key, { color: palette.grass, kind: 'land' });
}

function shade(color: Color, amount: number): string {
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v * amount)));
  return `rgb(${f(color[0])},${f(color[1])},${f(color[2])})`;
}

/** Draws a viewport-sized, deterministic slice. Coordinates are tile coordinates, not pixels. */
export function drawWorld(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  world: ProceduralWorld,
  cameraX: number,
  cameraY: number,
  tileSize: number,
): void {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#172330';
  ctx.fillRect(0, 0, width, height);
  const startX = Math.floor(cameraX);
  const startY = Math.floor(cameraY);
  const endX = Math.ceil(cameraX + width / tileSize);
  const endY = Math.ceil(cameraY + height / tileSize);
  const seed = world.seed | 0;

  for (let ty = startY; ty <= endY; ty++) {
    for (let tx = startX; tx <= endX; tx++) {
      const terrain = terrainAt(tx, ty, world);
      const px = Math.floor((tx - cameraX) * tileSize);
      const py = Math.floor((ty - cameraY) * tileSize);
      const size = Math.ceil(tileSize) + 1;
      ctx.fillStyle = shade(terrain.color, 0.91 + hash(tx, ty, seed + 12) * 0.14);
      ctx.fillRect(px, py, size, size);

      const detail = hash(tx, ty, seed + 1001);
      if (terrain.kind === 'water') {
        if (detail > 0.84) {
          ctx.fillStyle = 'rgba(184,226,235,0.34)';
          ctx.fillRect(px + tileSize * 0.18, py + tileSize * 0.3, tileSize * 0.36, Math.max(1, tileSize * 0.12));
        }
      } else if (terrain.kind === 'land' && detail > (world.biome === 'desert' ? 0.965 : 0.91)) {
        // Tiny, blocky trees and cacti, always anchored to the tile coordinate.
        const trunk = world.biome === 'desert' ? '#72553b' : '#74533a';
        ctx.fillStyle = trunk;
        ctx.fillRect(px + tileSize * 0.43, py + tileSize * 0.36, tileSize * 0.16, tileSize * 0.5);
        ctx.fillStyle = world.biome === 'desert' ? '#66864c' : '#286c45';
        ctx.fillRect(px + tileSize * 0.15, py + tileSize * 0.08, tileSize * 0.7, tileSize * 0.48);
        ctx.fillStyle = world.biome === 'desert' ? '#809451' : '#3c8952';
        ctx.fillRect(px + tileSize * 0.3, py + tileSize * 0.16, tileSize * 0.42, tileSize * 0.24);
      } else if (terrain.kind === 'land' && detail > 0.76 && tileSize >= 12) {
        ctx.fillStyle = world.biome === 'frozen' ? '#ecf1e4' : '#f4d875';
        ctx.fillRect(px + tileSize * 0.35, py + tileSize * 0.32, Math.max(2, tileSize * 0.12), Math.max(2, tileSize * 0.12));
      }
      if (tileSize >= 10) {
        ctx.strokeStyle = 'rgba(11,22,28,0.12)';
        ctx.lineWidth = Math.max(0.5, tileSize * 0.018);
        ctx.strokeRect(px + 0.5, py + 0.5, size - 1, size - 1);
      }
    }
  }
}

export function createWorldPreview(world: ProceduralWorld, width = 256, height = 160): string {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D недоступен');
  drawWorld(ctx, width, height, world, -13.2, -8.4, 4);
  return canvas.toDataURL('image/jpeg', 0.82);
}
