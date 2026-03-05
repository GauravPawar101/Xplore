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
    }
})
