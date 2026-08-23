import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Fixed output names, not content hashes. The embed document references these
// two files, and it is itself dynamic and short-cached (D9) — so the cache
// busting happens at the document, and the bundles stay predictable for the
// CSP's `script-src 'self'` and for the CDN's long TTL.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: 'src/main.jsx',
      output: {
        entryFileNames: 'widget.js',
        assetFileNames: 'widget.[ext]',
        // One bundle. A widget that loads a second chunk mid-flow is a widget
        // that breaks when a CDN is slow, in front of a customer who has already
        // lost money once today.
        manualChunks: undefined,
        inlineDynamicImports: true
      }
    }
  }
});
