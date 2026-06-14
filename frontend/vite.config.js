import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output goes into the Python package (src/vpv/web) so `vpv-view` can
// serve the SPA directly. In dev, proxy the API/media to the Python server.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../src/vpv/web',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/media': 'http://127.0.0.1:8000',
    },
  },
})
