import { defineConfig } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(here, '..')
const backendDir = path.join(repoRoot, 'backend')

const python = process.env.PYTHON_BIN ?? 'python'
const backendPort = Number(process.env.REVOLAB_E2E_BACKEND_PORT ?? 18021)
const backendUrl = `http://127.0.0.1:${backendPort}`
const frontendUrl = `http://127.0.0.1:${process.env.REVOLAB_E2E_FRONTEND_PORT ?? 4174}`

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: {
    baseURL: frontendUrl,
    trace: 'retain-on-failure',
  },
  expect: {
    timeout: 15_000,
  },
  webServer: [
    {
      // A real FastAPI backend over a real database: migrations are applied
      // from the backend working directory before uvicorn starts. CI points
      // REVOLAB_DATABASE_URL at the PostgreSQL service; local runs fall back
      // to a throwaway SQLite file.
      command: `${python} -m alembic upgrade head && ${python} -m uvicorn revolab.main:app --host 127.0.0.1 --port ${backendPort} --app-dir src`,
      cwd: backendDir,
      url: `${backendUrl}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        REVOLAB_DATABASE_URL: process.env.REVOLAB_DATABASE_URL ?? `sqlite:///./.playsmoke.db`,
        REVOLAB_CONTENT_ROOT: process.env.REVOLAB_CONTENT_ROOT ?? './.playsmoke-content',
        REVOLAB_CORS_ORIGINS: frontendUrl,
        // Opt-in in-process fake compute provider for the browser vertical slice.
        REVOLAB_E2E_FAKE_COMPUTE: '1',
      },
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${process.env.REVOLAB_E2E_FRONTEND_PORT ?? 4174} --strictPort`,
      cwd: here,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        VITE_API_PROXY_TARGET: backendUrl,
      },
    },
  ],
})
