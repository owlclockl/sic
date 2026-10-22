#!/usr/bin/env node
/**
 * Генерирует иконки приложения из одного рисунка (scripts/lib/app-icon.mjs):
 *
 *   public/icon-192.png, icon-512.png, icon-maskable-512.png, favicon.ico
 *     → PWA: «Добавить на главный экран» с нормальной иконкой;
 *   android/app/src/main/res/mipmap-.../ic_launcher...png
 *     → лаунчер Android (квадратная, круглая и adaptive-иконка).
 *
 * Запускается из `npm run icons`; для Android-сборки вызывается автоматически
 * перед `cap sync`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderAppIconPng, renderSplashPng, pngChunk, crc32 } from './lib/app-icon.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PUBLIC_DIR = path.join(ROOT, 'public');
const RES_DIR = path.join(ROOT, 'android', 'app', 'src', 'main', 'res');

/** Собирает .ico из PNG-разных размеров (формат ICO с PNG-содержимым). */
function buildIco(pngs) {
  const header = Buffer.alloc(6 + pngs.length * 16);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(pngs.length, 4);
  let offset = header.length;
  const buffers = [header];
  pngs.forEach(({ size, png }, i) => {
    const pos = 6 + i * 16;
    header[pos] = size >= 256 ? 0 : size;
    header[pos + 1] = size >= 256 ? 0 : size;
    header[pos + 2] = 0;
    header[pos + 3] = 0;
    header.writeUInt16LE(1, pos + 4);
    header.writeUInt16LE(32, pos + 6);
    header.writeUInt32LE(png.length, pos + 8);
    header.writeUInt32LE(offset, pos + 12);
    offset += png.length;
    buffers.push(png);
  });
  return Buffer.concat(buffers);
}

/** Экраны Android: базовая иконка 48dp, foreground для adaptive — 108dp. */
const ANDROID_DENSITIES = [
  { dir: 'mipmap-mdpi', launcher: 48, foreground: 108 },
  { dir: 'mipmap-hdpi', launcher: 72, foreground: 162 },
  { dir: 'mipmap-xhdpi', launcher: 96, foreground: 216 },
  { dir: 'mipmap-xxhdpi', launcher: 144, foreground: 324 },
  { dir: 'mipmap-xxxhdpi', launcher: 192, foreground: 432 },
];

function writePwaIcons() {
  fs.mkdirSync(PUBLIC_DIR, { recursive: true });
  const files = [
    ['icon-192.png', renderAppIconPng(192, 'square')],
    ['icon-512.png', renderAppIconPng(512, 'square')],
    ['icon-maskable-512.png', renderAppIconPng(512, 'maskable')],
    [
      'favicon.ico',
      buildIco([
        { size: 16, png: renderAppIconPng(16, 'square') },
        { size: 32, png: renderAppIconPng(32, 'square') },
        { size: 48, png: renderAppIconPng(48, 'square') },
      ]),
    ],
  ];
  for (const [name, data] of files) {
    fs.writeFileSync(path.join(PUBLIC_DIR, name), data);
    console.log(`  public/${name} — ${data.length} байт`);
  }
}

function writeManifest() {
  const manifest = {
    name: 'Reveal Atlas',
    short_name: 'Reveal Atlas',
    description:
      'Карта мира, которая открывается по клеткам. Загрузите любую картинку, настройте размер квадратов и открывайте мир по мере прохождения.',
    // Относительные пути: манифест работает и на сервере, и в WebView.
    start_url: './',
    scope: './',
    display: 'standalone',
    orientation: 'any',
    background_color: '#080d16',
    theme_color: '#080d16',
    lang: 'ru',
    icons: [
      { src: './icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: './icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: './icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
  const file = path.join(PUBLIC_DIR, 'manifest.webmanifest');
  fs.writeFileSync(file, JSON.stringify(manifest, null, 2) + '\n');
  console.log('  public/manifest.webmanifest');
}

/** Splash-экраны Android: портрет и ландшафт для каждой плотности. */
const SPLASH_SCREENS = [
  ['drawable-port-mdpi', 320, 480],
  ['drawable-port-hdpi', 480, 800],
  ['drawable-port-xhdpi', 720, 1280],
  ['drawable-port-xxhdpi', 960, 1600],
  ['drawable-port-xxxhdpi', 1280, 1920],
  ['drawable-land-mdpi', 480, 320],
  ['drawable-land-hdpi', 800, 480],
  ['drawable-land-xhdpi', 1280, 720],
  ['drawable-land-xxhdpi', 1600, 960],
  ['drawable-land-xxxhdpi', 1920, 1280],
  ['drawable', 480, 320],
];

function writeAndroidSplash() {
  if (!fs.existsSync(RES_DIR)) return;
  for (const [dir, width, height] of SPLASH_SCREENS) {
    const target = path.join(RES_DIR, dir);
    if (!fs.existsSync(target)) continue;
    fs.writeFileSync(path.join(target, 'splash.png'), renderSplashPng(width, height));
    console.log(`  android res/${dir}/splash.png — ${width}x${height}`);
  }
}

function writeAndroidIcons() {
  if (!fs.existsSync(RES_DIR)) {
    console.log('  android/ ещё не создан — пропускаю (npm run android:icons после cap add android)');
    return;
  }
  for (const { dir, launcher, foreground } of ANDROID_DENSITIES) {
    const target = path.join(RES_DIR, dir);
    fs.mkdirSync(target, { recursive: true });
    const files = [
      ['ic_launcher.png', renderAppIconPng(launcher, 'square')],
      ['ic_launcher_round.png', renderAppIconPng(launcher, 'round')],
      ['ic_launcher_foreground.png', renderAppIconPng(foreground, 'foreground')],
    ];
    for (const [name, data] of files) {
      fs.writeFileSync(path.join(target, name), data);
    }
    console.log(`  android res/${dir}/ — ic_launcher ${launcher}px, foreground ${foreground}px`);
  }
}

console.log('Генерирую иконки Reveal Atlas…');
writePwaIcons();
writeManifest();
writeAndroidIcons();
writeAndroidSplash();
console.log('Готово.');
void pngChunk;
void crc32;
