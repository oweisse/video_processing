import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Dev: `npm run dev` on 5173 proxies the API to clean-audio-gui on 8787.
// Build: written into the Python package, served by FastAPI at /.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../video_audio_cleaner/gui/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8787",
      "/media": "http://127.0.0.1:8787",
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
});
