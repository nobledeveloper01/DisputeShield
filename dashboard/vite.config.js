import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Unlike the widget, this is our own surface on our own origin, so hashed
// filenames are fine and long caching is free. The one thing kept from the
// widget's config is the refusal to load anything from a third-party origin:
// an operations console that stops working when a CDN is slow is a console
// nobody can use during the incident they need it for.
export default defineConfig({
  plugins: [react()],
  root: '.',
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 4190 }
});
