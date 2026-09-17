import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
  plugins: [react()],
  server: { proxy: { '/api': { target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, xfwd: true } } },
  build: {
    rollupOptions: {
      output: {
        // Keep third-party code stable and separately cacheable. The admin surface
        // uses a broad set of Ant Design controls, so split its largest families
        // instead of shipping one invalidation-prone vendor bundle.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('/node_modules/@ant-design/icons/')) return 'antd-icons';
          if (id.includes('/node_modules/@ant-design/v5-patch-for-react-19/')) return 'antd';
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/') || id.includes('/node_modules/scheduler/')) return 'react-runtime';
          if (id.includes('/node_modules/antd/')) return 'antd';
          return 'vendor';
        },
      },
    },
  },
  };
});
