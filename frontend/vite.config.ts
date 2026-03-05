import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  root: '.',
  publicDir: 'public',
  server: {
    port: 8000
  },
  build: {
    outDir: 'dist',
    sourcemap: false
  }
})
