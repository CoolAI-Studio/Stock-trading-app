/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
  readonly VITE_WS_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/** 建置期注入的 commit。見 vite.config.ts 和 src/lib/buildInfo.ts。 */
declare const __APP_COMMIT__: string
