/**
 * Демонстрационный мир: процедурная карта, которую можно открывать клетка за клеткой.
 * Рисуется один раз при первом запуске и сохраняется как обычная картинка,
 * поэтому её можно заменить любым файлом.
 */

function hash(x: number, y: number, seed: number): number {
  let h = seed ^ Math.imul(x, 374761393) ^ Math.imul(y, 668265263);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h = h ^ (h >>> 16);
  return (h >>> 0) / 4294967295;
}

function smooth(t: number): number {
  return t * t * (3 - 2 * t);
}

function valueNoise(x: number, y: number, seed: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const xf = smooth(x - xi);
  const yf = smooth(y - yi);
  const a = hash(xi, yi, seed);
  const b = hash(xi + 1, yi, seed);
  const c = hash(xi, yi + 1, seed);
  const d = hash(xi + 1, yi + 1, seed);
  const top = a + (b - a) * xf;
  const bottom = c + (d - c) * xf;
  return top + (bottom - top) * yf;
}

function fbm(x: number, y: number, seed: number, octaves = 5): number {
  let sum = 0;
  let amp = 1;
  let norm = 0;
  let freq = 1;
  for (let i = 0; i < octaves; i++) {
    sum += valueNoise(x * freq, y * freq, seed + i * 1013) * amp;
    norm += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return sum / norm;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function mix(c1: number[], c2: number[], t: number): number[] {
  const k = Math.min(1, Math.max(0, t));
  return [lerp(c1[0], c2[0], k), lerp(c1[1], c2[1], k), lerp(c1[2], c2[2], k)];
}

const DEEP_WATER = [18, 46, 74];
const SHALLOW_WATER = [52, 112, 146];
const COAST = [214, 197, 152];
const LOWLAND = [104, 143, 92];
const FOREST = [62, 104, 68];
const HIGHLAND = [128, 122, 96];
const ROCK = [140, 132, 124];
const SNOW = [238, 240, 242];

function terrainColor(elevation: number, moisture: number, latitude: number): number[] {
  if (elevation < 0.34) return mix(DEEP_WATER, SHALLOW_WATER, elevation / 0.34);
  if (elevation < 0.38) return mix(SHALLOW_WATER, COAST, (elevation - 0.34) / 0.04);
  if (elevation < 0.44) return mix(COAST, LOWLAND, (elevation - 0.38) / 0.06);
  if (elevation < 0.62) return mix(LOWLAND, FOREST, (moisture - 0.35) / 0.5);
  if (elevation < 0.75) return mix(FOREST, HIGHLAND, (elevation - 0.62) / 0.13);
  if (elevation < 0.86) return mix(HIGHLAND, ROCK, (elevation - 0.75) / 0.11);
  const snowLine = 0.9 - Math.abs(latitude) * 0.22;
  return mix(ROCK, SNOW, (elevation - 0.86) / Math.max(0.02, 1 - snowLine));
}

export interface DemoWorld {
  dataUrl: string;
  width: number;
  height: number;
}

export function createDemoWorld(width = 1600, height = 1000): DemoWorld {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D недоступен');

  // Поле высот считаем в половинном разрешении — быстрее, а апскейл сглаживает берега.
  const fieldW = Math.round(width / 2);
  const fieldH = Math.round(height / 2);
  const field = document.createElement('canvas');
  field.width = fieldW;
  field.height = fieldH;
  const fctx = field.getContext('2d');
  if (!fctx) throw new Error('Canvas 2D недоступен');

  const img = fctx.createImageData(fieldW, fieldH);
  const seed = 81351;
  for (let py = 0; py < fieldH; py++) {
    for (let px = 0; px < fieldW; px++) {
      const u = px / fieldW;
      const v = py / fieldH;
      const latitude = Math.abs(v - 0.5) * 2;

      const base = fbm(u * 4.1 + 11.7, v * 3.4 + 4.2, seed, 5);
      const detail = fbm(u * 13.3 + 3.1, v * 11.7 + 9.4, seed + 77, 4);
      const ridge = 1 - Math.abs(fbm(u * 6.4 + 21.3, v * 5.1 + 14.8, seed + 401, 4) * 2 - 1);

      // Континентальность: ближе к центру материков больше.
      const cx = (u - 0.5) * 2;
      const cy = (v - 0.5) * 2;
      const falloff = 1 - Math.pow(Math.min(1, Math.sqrt(cx * cx * 0.85 + cy * cy * 1.15)), 1.9);
      const continent = base * 0.62 + falloff * 0.5 + detail * 0.1 - 0.22;
      const elevation = Math.min(1, Math.max(0, continent * 0.9 + Math.pow(ridge, 3) * 0.22 - latitude * 0.03));
      const moisture = fbm(u * 5.6 + 31.2, v * 4.4 + 7.7, seed + 900, 3);

      const c = terrainColor(elevation, moisture, latitude);
      const shade = 0.94 + detail * 0.12;
      const idx = (py * fieldW + px) * 4;
      img.data[idx] = Math.min(255, c[0] * shade);
      img.data[idx + 1] = Math.min(255, c[1] * shade);
      img.data[idx + 2] = Math.min(255, c[2] * shade);
      img.data[idx + 3] = 255;
    }
  }
  fctx.putImageData(img, 0, 0);

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(field, 0, 0, width, height);

  // Виньетка по краям — мир выглядит как лист атласа.
  const vignette = ctx.createRadialGradient(
    width / 2,
    height / 2,
    Math.min(width, height) * 0.35,
    width / 2,
    height / 2,
    Math.max(width, height) * 0.75,
  );
  vignette.addColorStop(0, 'rgba(8, 18, 28, 0)');
  vignette.addColorStop(1, 'rgba(8, 18, 28, 0.55)');
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, width, height);

  // Координатная сетка атласа.
  ctx.strokeStyle = 'rgba(232, 240, 245, 0.08)';
  ctx.lineWidth = 1;
  const step = width / 32;
  for (let x = 0; x <= width; x += step) {
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, 0);
    ctx.lineTo(Math.round(x) + 0.5, height);
    ctx.stroke();
  }
  for (let y = 0; y <= height; y += step) {
    ctx.beginPath();
    ctx.moveTo(0, Math.round(y) + 0.5);
    ctx.lineTo(width, Math.round(y) + 0.5);
    ctx.stroke();
  }

  // Подписи «регионов» — просто чтобы карта читалась.
  const labels: Array<[string, number, number, number]> = [
    ['СЕВЕРНЫЙ ПРЕДЕЛ', 0.34, 0.12, 34],
    ['АВАЛОН', 0.2, 0.46, 46],
    ['МОРЕ ТИШИНЫ', 0.63, 0.36, 30],
    ['ХРЕБЕТ АРГЕНТ', 0.72, 0.62, 26],
    ['ЗЕМЛИ КАЙРА', 0.31, 0.74, 38],
    ['ЮЖНЫЙ ОКЕАН', 0.56, 0.9, 28],
  ];
  ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(240, 246, 250, 0.62)';
  for (const [text, fx, fy, size] of labels) {
    ctx.font = `600 ${size}px "Georgia", "Times New Roman", serif`;
    ctx.fillText(text, fx * width, fy * height);
  }

  // Компас.
  const cxc = width - 130;
  const cyc = height - 130;
  ctx.strokeStyle = 'rgba(240, 246, 250, 0.5)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cxc, cyc, 42, 0, Math.PI * 2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cxc, cyc - 58);
  ctx.lineTo(cxc + 12, cyc);
  ctx.lineTo(cxc, cyc + 58);
  ctx.lineTo(cxc - 12, cyc);
  ctx.closePath();
  ctx.fillStyle = 'rgba(240, 246, 250, 0.5)';
  ctx.fill();
  ctx.font = '600 20px "Georgia", serif';
  ctx.fillText('N', cxc, cyc - 68);

  return { dataUrl: canvas.toDataURL('image/jpeg', 0.86), width, height };
}
