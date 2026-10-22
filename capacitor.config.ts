import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.revealatlas.app',
  appName: 'Reveal Atlas',
  webDir: 'dist',
  // Приложение открывается с https://localhost внутри WebView, поэтому адрес
  // онлайн-сервера задаётся на этапе сборки (VITE_SERVER_URL) или вручную
  // в панели «Онлайн» — origin приложения сервером не является.
  server: {
    androidScheme: 'https',
    hostname: 'localhost',
    // Сервер может работать по http (локальная сеть, туннель без TLS).
    cleartext: true,
  },
};

export default config;
