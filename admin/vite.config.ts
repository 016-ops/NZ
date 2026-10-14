/** 开发服务器只监听回环；代理保持浏览器侧相对 URL，所有业务认证仍由 Django 完成。 */
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  server: {
    host: '127.0.0.1',
    proxy: Object.fromEntries(['/api', '/health'].map(prefix => [prefix, {
      target: process.env.NZ_API_TARGET || 'http://127.0.0.1:8765',
      changeOrigin: true,
      // 代理只重写本地开发页的 Origin；未知来源仍交由后端拒绝。
      configure(proxy) {
        proxy.on('proxyReq', (request, original) => {
          if (original.headers.origin === 'http://127.0.0.1:5173' || original.headers.origin === 'http://localhost:5173') {
            request.setHeader('Origin', process.env.NZ_API_TARGET || 'http://127.0.0.1:8765')
          }
        })
      },
    }])),
  },
})
