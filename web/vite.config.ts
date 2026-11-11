import { createReadStream, existsSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const inbox = fileURLToPath(new URL('../data/inbox/', import.meta.url))

function mockFiles(): Plugin {
  return {
    name: 'payoutguard-mock-files',
    configureServer(server) {
      server.middlewares.use('/files', (request, response) => {
        let name: string
        try { name = decodeURIComponent(new URL(request.url ?? '/', 'http://localhost').pathname.slice(1)) }
        catch { response.writeHead(400).end(); return }
        if (!/^[^/\\]+\.(pdf|jpg)$/i.test(name) || name.startsWith('.') || !existsSync(join(inbox, name))) {
          response.writeHead(404).end()
          return
        }
        response.setHeader('Content-Type', name.endsWith('.pdf') ? 'application/pdf' : 'image/jpeg')
        createReadStream(join(inbox, name)).pipe(response)
      })
    },
  }
}

export default defineConfig(({ command, mode }) => {
  const mock = command === 'serve' && loadEnv(mode, process.cwd()).VITE_USE_MOCK === 'true'
  return {
    plugins: [react(), tailwindcss(), ...(mock ? [mockFiles()] : [])],
    server: mock ? undefined : {
      proxy: { '/api': 'http://127.0.0.1:8000', '/files': 'http://127.0.0.1:8000' },
    },
  }
})
