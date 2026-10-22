#!/usr/bin/env node
/**
 * Печатает адрес, по которому приложение открывается с телефона,
 * и рисует QR-код для сканирования камерой.
 *
 *   npm run phone              # порт 8080 (как у npm run online)
 *   npm run phone -- --port 5173
 *
 * Адрес берётся из переменных песочницы (E2B_SANDBOX_ID) или из --host.
 * Если запускаете не в песочнице — укажите адрес явно:
 *   node scripts/phone-access.mjs --url https://my-atlas.example.com
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import QRCode from 'qrcode';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function parseArgs(argv) {
  const options = { port: 8080, url: null, out: 'phone-access.png' };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--port' || argv[i] === '-p') options.port = Number(argv[++i]);
    else if (argv[i] === '--url' || argv[i] === '-u') options.url = argv[++i];
    else if (argv[i] === '--out' || argv[i] === '-o') options.out = argv[++i];
  }
  return options;
}

const args = parseArgs(process.argv);

const sandboxId = process.env.E2B_SANDBOX_ID;
let url = args.url;
if (!url) {
  if (sandboxId) url = `https://${args.port}-${sandboxId}.e2b.app`;
  else url = `http://localhost:${args.port}`;
}
url = url.replace(/\/+$/, '');

const outPath = path.join(ROOT, args.out);
await QRCode.toFile(outPath, url, {
  width: 720,
  margin: 2,
  color: { dark: '#0b1220ff', light: '#ffffffff' },
  errorCorrectionLevel: 'M',
});

console.log('');
console.log('  Reveal Atlas — открыть с телефона');
console.log('');
console.log(`  Адрес:   ${url}`);
console.log(`  QR-код:  ${path.relative(ROOT, outPath)}`);
console.log('');
console.log('  1. Откройте камеру телефона и наведите на QR-код (или перейдите по адресу).');
console.log('  2. Нажмите «Онлайн» в нижней панели → «+ Создать комнату из этого мира».');
console.log('  3. Отправьте другу код комнаты или ссылку-приглашение.');
console.log('');
if (sandboxId) {
  console.log('  Онлайн-режим работает сразу: сервер и WebSocket на том же адресе.');
  console.log('  Если превью-прокси не пробросит WebSocket — в поле «Сервер» впишите');
  console.log('  адрес своего сервера (npm run online:tunnel).');
  console.log('');
}
void fs;
