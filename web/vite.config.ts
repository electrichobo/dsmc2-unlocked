import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

// Relative base: the build works from any path (GitHub Pages project site,
// a local `vite preview`, or a file server) without rebuilding.
export default defineConfig({
  base: "./",
  plugins: [solid()],
  worker: { format: "es" },
  build: { target: "es2022" },
});
