import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
// Port 5173 preferred; if busy, Vite tries 5174, 5175, … (must match backend CORS FRONTEND_PORTS)
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: { '@': path.resolve(__dirname, 'src') },
    },
    server: {
        host: '0.0.0.0',
        port: 5173,
        strictPort: false,
        proxy: {
            '/ws': { target: 'ws://localhost:8000', ws: true },
            '/jobs': { target: 'http://localhost:8000' },
            '/graph': { target: 'http://localhost:8000' },
            '/analyze': { target: 'http://localhost:8000' },
            '/files': { target: 'http://localhost:8000' },
            '/analyses': { target: 'http://localhost:8000' },
            '/explain': { target: 'http://localhost:8000' },
            '/program': { target: 'http://localhost:8000' },
            '/generate': { target: 'http://localhost:8000' },
            '/generated': { target: 'http://localhost:8000' },
            '/rag': { target: 'http://localhost:8000' },
            '/health': { target: 'http://localhost:8000' },
            '/internal': { target: 'http://localhost:8000' },
        },
    }
})
