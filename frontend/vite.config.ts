/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
// 這一次建置的 commit，注入成一個建置期常數。
//
// Vercel 會給 VERCEL_GIT_COMMIT_SHA；自己建的話設 APP_GIT_COMMIT。兩個都沒有就是
// 空字串，而 buildInfo.ts 會把它讀成「不知道」——**不是「最新」**。
const APP_COMMIT =
  process.env.VERCEL_GIT_COMMIT_SHA ?? process.env.APP_GIT_COMMIT ?? ''

export default defineConfig({
  define: {
    __APP_COMMIT__: JSON.stringify(APP_COMMIT),
  },
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // The one test that drives a REAL browser lives outside this run. It costs
    // a Chromium process (~300 MB) and this project's machine decides whether
    // a suite finishes on its last few GB. `npm run test:chart` runs it, and
    // so does its own CI job.
    exclude: ['**/node_modules/**', '**/dist/**', '**/*.browser.test.ts'],
    globals: true,
    // The default "forks" pool (child_process-based) hangs/times out on
    // this Windows environment, likely related to the working directory
    // path containing spaces. "threads" (worker_threads-based) works.
    pool: 'threads',
  },
})
