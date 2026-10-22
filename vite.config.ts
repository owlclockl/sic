import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { attachToViteServer } from './server/online.mjs';

const rootDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * Настоящий онлайн-режим прямо в dev-сервере: /api/* и WebSocket /ws
 * работают на том же порту, что и приложение.
 */
function onlineMode(): Plugin {
  return {
    name: 'reveal-atlas-online',
    configureServer(server) {
      attachToViteServer(server, { dataDir: path.join(rootDir, 'data', 'online') });
      server.config.logger.info('  ➜  онлайн-режим: ws на том же порту (/ws), комнаты в data/online');
    },
  };
}

export default defineConfig({
  plugins: [react(), onlineMode()],
  // Относительные пути к ассетам: сборка работает и на сервере, и в WebView
  // (Android/Capacitor открывает её с https://localhost, а не с корня домена).
  base: './',
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: true,
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    allowedHosts: true,
  },
});
