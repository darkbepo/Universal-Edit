// Builds release/MediaConverter-web-local/ (+ .zip): the built site in app/ plus launchers.
import { cpSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { zipSync } from "fflate";

const web = fileURLToPath(new URL("..", import.meta.url));
const name = "MediaConverter-web-local";
const out = join(web, "release", name);
rmSync(join(web, "release"), { recursive: true, force: true });
mkdirSync(out, { recursive: true });
cpSync(join(web, "dist"), join(out, "app"), { recursive: true });
for (const f of readdirSync(join(web, "local"))) {
  const text = readFileSync(join(web, "local", f), "utf8");
  // Windows batch files must use CRLF line endings.
  writeFileSync(join(out, f), f.endsWith(".bat") ? text.replace(/\r?\n/g, "\r\n") : text);
}
cpSync(join(web, "..", "LICENSE"), join(out, "LICENSE.txt"));

const files = {};
const walk = (dir) => {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p);
    else files[`${name}/${relative(out, p).replaceAll("\\", "/")}`] = [readFileSync(p), { level: p.endsWith(".wasm") ? 6 : 9 }];
  }
};
walk(out);
writeFileSync(join(web, "release", `${name}.zip`), zipSync(files));
console.log(`release/${name}.zip`);
