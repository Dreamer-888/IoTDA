import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    base: './',
    server: {
      port: 5173,
      strictPort: true,
      proxy: { '/api': { target: env.API_TARGET || 'http://127.0.0.1:5000', changeOrigin: true, ws: true } },
    },
    build: {
      rolldownOptions: { output: { manualChunks: (id) => {
        if (id.includes('/three/')) return 'three';
        if (id.includes('/echarts/') || id.includes('/zrender/')) return 'charts';
      } } },
    },
  };
});
