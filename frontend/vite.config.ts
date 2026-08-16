/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    // The default "forks" pool (child_process-based) hangs/times out on
    // this Windows environment, likely related to the working directory
    // path containing spaces. "threads" (worker_threads-based) works.
    pool: 'threads',
  },
})
