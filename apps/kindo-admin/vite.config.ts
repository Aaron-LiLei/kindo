/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    // 构建产物由 hub 挂载在 /admin/ 路径下，资源引用必须带此前缀（否则浏览器请求 /assets/* 404 白屏）
    base: '/admin/',
    server: {
      // 开发代理目标可用 KINDO_DEV_PROXY 覆盖（默认 8090；本机复验环境为 18090）
      proxy: {
        '/api': env.KINDO_DEV_PROXY || 'http://127.0.0.1:8090',
      },
    },
    build: {
      outDir: 'dist',
      chunkSizeWarningLimit: 1200,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            antd: ['antd', '@ant-design/icons', 'dayjs'],
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
    },
  }
})
