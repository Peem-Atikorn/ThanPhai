// MapLibre v6 loads its Web Worker from a URL. Bundlers (Turbopack) do not serve that file, so the
// worker fails with "non-JavaScript MIME type". Copy it to public/ to keep it in step with the package.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
mkdirSync(join(root, "public"), { recursive: true });
copyFileSync(
  join(root, "node_modules", "maplibre-gl", "dist", "maplibre-gl-worker.mjs"),
  join(root, "public", "maplibre-gl-worker.mjs"),
);
console.log("copied maplibre-gl-worker.mjs to public/");
