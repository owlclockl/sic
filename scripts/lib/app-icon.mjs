/**
 * Мини-кодировщик PNG и рисование иконки Reveal Atlas.
 * Ноль зависимостей: используется и сборкой одного файла (RevealAtlas.cmd),
 * и генерацией иконок для PWA / Android.
 *
 * Иконка — та же, что и фавикон: тёмный скруглённый квадрат с четырьмя
 * клетками, две из которых «открыты» (бирюзовые).
 */
import zlib from 'node:zlib';

export const ICON_COLORS = {
  bg: [11, 18, 32],      // #0b1220
  teal: [126, 224, 195], // #7ee0c3
  slate: [42, 58, 82],   // #2a3a52
};

// --- CRC32 & Minimal PNG / ICO Encoder (zero external dependencies) ---

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii');
  const lenBuf = Buffer.alloc(4);
  lenBuf.writeUInt32BE(data.length, 0);
  const crcInput = Buffer.concat([typeBuf, data]);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(crcInput), 0);
  return Buffer.concat([lenBuf, typeBuf, data, crcBuf]);
}

function encodeRgbaPng(width, height, rgba) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // 8 bits per channel
  ihdr[9] = 6; // RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    const rowOffset = y * (stride + 1);
    raw[rowOffset] = 0; // Filter type 0 (None)
    rgba.copy(raw, rowOffset + 1, y * stride, (y + 1) * stride);
  }

  const compressed = zlib.deflateSync(raw, { level: 9 });
  return Buffer.concat([
    signature,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', compressed),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function insideRoundedRect(px, py, x, y, w, h, r) {
  if (px < x || px > x + w || py < y || py > y + h) return false;
  const cx = Math.max(x + r, Math.min(x + w - r, px));
  const cy = Math.max(y + r, Math.min(y + h - r, py));
  const dx = px - cx;
  const dy = py - cy;
  return dx * dx + dy * dy <= r * r;
}

/**
 * Рисует фирменную иконку Reveal Atlas в координатах 32x32 с 4x4 суперсэмплингом.
 */

/** Круглая маска — для иконки ic_launcher_round. */
function insideCircle(px, py, size) {
  const dx = px - size / 2;
  const dy = py - size / 2;
  return dx * dx + dy * dy <= (size / 2) * (size / 2);
}

/**
 * Маскаble-иконка: фон заливает весь квадрат (маска Android/iOS срезает углы),
 * клетки прижаты к центру — в safe zone 80%.
 */
function isFullBleed(shape) {
  return shape === 'maskable';
}

/** Какого цвета клетка в точке (ux, uy) сетки 32x32. */
function cellColor(ux, uy, teal, slate) {
  if (insideRoundedRect(ux, uy, 6, 6, 9, 9, 1.6) || insideRoundedRect(ux, uy, 17, 17, 9, 9, 1.6)) {
    return teal;
  }
  if (insideRoundedRect(ux, uy, 17, 6, 9, 9, 1.6) || insideRoundedRect(ux, uy, 6, 17, 9, 9, 1.6)) {
    return slate;
  }
  return null;
}

/**
 * Рисует иконку в размере size×size (4x4 суперсэмплинг).
 *
 * shape:
 *   'square'     — полноценная иконка на тёмном фоне (PWA, ic_launcher);
 *   'round'      — то же, обрезанное по кругу (ic_launcher_round);
 *   'foreground' — прозрачный фон, только клетки в центральных 62%
 *                  (adaptive icon foreground: safe zone 66/108).
 */
export function renderAppIconPng(size, shape = 'square') {
  const rgba = Buffer.alloc(size * size * 4);
  const { bg, teal, slate } = ICON_COLORS;
  const samples = 4;
  const totalSamples = samples * samples;
  const scale = shape === 'foreground' ? 0.62 : 1;
  const offset = (1 - scale) / 2;

  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      let rSum = 0;
      let gSum = 0;
      let bSum = 0;
      let aSum = 0;

      for (let sy = 0; sy < samples; sy++) {
        const uy = ((py + (sy + 0.5) / samples) / size) * 32;
        for (let sx = 0; sx < samples; sx++) {
          const ux = ((px + (sx + 0.5) / samples) / size) * 32;
          if (!isFullBleed(shape) && !insideRoundedRect(ux, uy, 0.5, 0.5, 31, 31, 7.5)) continue;
          if (shape === 'round' && !insideCircle(ux, uy, 32)) continue;

          const color = cellColor(offset * 32 + ux * scale, offset * 32 + uy * scale, teal, slate);
          if (color === null) {
            if (shape !== 'foreground') {
              rSum += bg[0];
              gSum += bg[1];
              bSum += bg[2];
              aSum += 255;
            }
            continue;
          }
          rSum += color[0];
          gSum += color[1];
          bSum += color[2];
          aSum += 255;
        }
      }

      const idx = (py * size + px) * 4;
      if (aSum > 0) {
        const hits = aSum / 255;
        rgba[idx] = Math.round(rSum / hits);
        rgba[idx + 1] = Math.round(gSum / hits);
        rgba[idx + 2] = Math.round(bSum / hits);
        rgba[idx + 3] = Math.round(aSum / totalSamples);
      }
    }
  }

  return encodeRgbaPng(size, size, rgba);
}

/**
 * Splash-экран: плотный фон приложения с иконкой по центру.
 * Нужен Android (пока WebView грузится) — своя картинка вместо заглушки.
 */
export function renderSplashPng(width, height, iconRatio = 0.28) {
  const rgba = Buffer.alloc(width * height * 4);
  const { bg, teal, slate } = ICON_COLORS;
  const iconSize = Math.max(16, Math.round(Math.min(width, height) * iconRatio));
  const x0 = Math.round((width - iconSize) / 2);
  const y0 = Math.round((height - iconSize) / 2);
  const samples = 4;

  for (let py = 0; py < height; py++) {
    for (let px = 0; px < width; px++) {
      const idx = (py * width + px) * 4;
      let r = bg[0];
      let g = bg[1];
      let b = bg[2];

      if (px >= x0 && px < x0 + iconSize && py >= y0 && py < y0 + iconSize) {
        let rSum = 0;
        let gSum = 0;
        let bSum = 0;
        let aSum = 0;
        for (let sy = 0; sy < samples; sy++) {
          const uy = ((py - y0 + (sy + 0.5) / samples) / iconSize) * 32;
          for (let sx = 0; sx < samples; sx++) {
            const ux = ((px - x0 + (sx + 0.5) / samples) / iconSize) * 32;
            if (!insideRoundedRect(ux, uy, 0.5, 0.5, 31, 31, 7.5)) continue;
            const color = cellColor(ux, uy, teal, slate) ?? bg;
            rSum += color[0];
            gSum += color[1];
            bSum += color[2];
            aSum += 255;
          }
        }
        if (aSum > 0) {
          const hits = aSum / 255;
          r = Math.round(rSum / hits);
          g = Math.round(gSum / hits);
          b = Math.round(bSum / hits);
        }
      }

      rgba[idx] = r;
      rgba[idx + 1] = g;
      rgba[idx + 2] = b;
      rgba[idx + 3] = 255;
    }
  }

  return encodeRgbaPng(width, height, rgba);
}

export { crc32, pngChunk, encodeRgbaPng, insideRoundedRect };
