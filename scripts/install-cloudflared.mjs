#!/usr/bin/env node
/**
 * Скачивает cloudflared из реестра npm и кладёт его в .tools/cloudflared.
 *
 * Зачем так: официальные релизы cloudflared лежат на GitHub Releases,
 * который не всегда доступен (корпоративные сети, песочницы). Пакеты npm
 * кладут тот же бинарник к себе, а registry.npmjs.org доступен почти везде.
 *
 * На своём компьютере лучше поставить официальную сборку:
 *   Windows:  winget install --id Cloudflare.cloudflared
 *   macOS:    brew install cloudflared
 *   Linux:    https://github.com/cloudflare/cloudflared/releases
 * Тогда этот скрипт не нужен — online-tunnel.mjs найдёт cloudflared в PATH.
 *
 *   node scripts/install-cloudflared.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TOOLS_DIR = path.join(ROOT, '.tools');
const TARGET = path.join(TOOLS_DIR, process.platform === 'win32' ? 'cloudflared.exe' : 'cloudflared');

/** Пакеты npm, в которых лежит готовый бинарник cloudflared под платформу. */
const SOURCES = {
  'linux-x64': ['agent-remoteops-cloudflared-linux-x64', '@farthershore/cloudflared-linux-x64'],
  'linux-arm64': ['agent-remoteops-cloudflared-linux-arm64'],
  'darwin-arm64': ['agentroam-cloudflared-darwin-arm64'],
  'darwin-x64': ['agentroam-cloudflared-darwin-x64'],
  'win32-x64': ['agentroam-cloudflared-win32-x64', 'agent-remoteops-cloudflared-win32-x64'],
};

function platformKey() {
  return `${process.platform}-${process.arch}`;
}

async function fetchTarball(pkg) {
  const tmp = fs.mkdtempSync(path.join(ROOT, '.tools-tmp-'));
  try {
    execFileSync('npm', ['pack', pkg, '--silent'], { cwd: tmp, stdio: 'pipe' });
    const tarball = fs.readdirSync(tmp).find((name) => name.endsWith('.tgz'));
    if (!tarball) throw new Error('npm pack не вернул архив');
    return { tarball: path.join(tmp, tarball), tmp };
  } catch (err) {
    fs.rmSync(tmp, { recursive: true, force: true });
    throw err;
  }
}

async function main() {
  if (fs.existsSync(TARGET)) {
    try {
      const version = execFileSync(TARGET, ['--version'], { encoding: 'utf8' }).trim();
      console.log(`cloudflared уже установлен: ${TARGET}`);
      console.log(version);
      return;
    } catch {
      /* битый файл — переустановим */
    }
  }

  const key = platformKey();
  const packages = SOURCES[key];
  if (!packages) {
    console.error(`Не знаю пакет с cloudflared для платформы ${key}.`);
    console.error('Поставьте cloudflared официально: https://github.com/cloudflare/cloudflared/releases');
    process.exit(1);
  }

  fs.mkdirSync(TOOLS_DIR, { recursive: true });
  for (const pkg of packages) {
    console.log(`Пробую ${pkg}…`);
    let tmp = null;
    try {
      const { tarball, tmp: dir } = await fetchTarball(pkg);
      tmp = dir;
      execFileSync('tar', ['xzf', tarball, '-C', tmp, '--strip-components=2', 'package/bin/cloudflared'], {
        stdio: 'pipe',
      });
      const extracted = path.join(tmp, 'cloudflared');
      if (!fs.existsSync(extracted)) throw new Error('в архиве нет bin/cloudflared');
      fs.copyFileSync(extracted, TARGET);
      fs.chmodSync(TARGET, 0o755);
      const version = execFileSync(TARGET, ['--version'], { encoding: 'utf8' }).trim();
      console.log(`Готово: ${TARGET}`);
      console.log(version);
      console.log('Теперь: npm run online:tunnel');
      return;
    } catch (err) {
      console.warn(`  не подошло: ${err instanceof Error ? err.message : err}`);
    } finally {
      if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
    }
  }

  console.error('Не удалось скачать cloudflared из npm.');
  console.error('Поставьте его официально: winget install Cloudflare.cloudflared / brew install cloudflared');
  process.exit(1);
}

void main();
