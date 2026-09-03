import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // 백엔드가 붙으면 VITE_API_BASE_URL 대신 이 프록시를 써도 됩니다.
    // proxy: { '/api': { target: 'http://192.168.0.100:8080', changeOrigin: true } },
  },
});
