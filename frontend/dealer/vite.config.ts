import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const allowedHosts = (env.VITE_ALLOWED_HOSTS || 'localhost,127.0.0.1').split(',').map(host => host.trim()).filter(Boolean);
  return { plugins: [vue()], server: { allowedHosts, proxy: { '/api': { target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, xfwd: true } } } };
});
