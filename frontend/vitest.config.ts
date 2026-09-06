import { defineConfig } from "vitest/config";

export default defineConfig({
  // The components use the automatic JSX runtime -- Next configures it, so
  // they do not import React. esbuild defaults to the classic runtime, which
  // would need that import and fails with "React is not defined".
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    // The deployed demo is served over https, and some behaviour depends on it:
    // a plain http backend is blocked there as mixed content. jsdom defaults to
    // http, which would hide that.
    environmentOptions: { jsdom: { url: "https://reporeviewer.example/" } },
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
});
