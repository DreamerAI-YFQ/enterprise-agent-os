import { spawnSync } from "node:child_process";
import {
  existsSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(scriptsDir, "..");
const generatedPath = resolve(
  frontendDir,
  "shared/src/api/generated/openapi.d.ts",
);
const existed = existsSync(generatedPath);
const before = existed ? readFileSync(generatedPath, "utf8") : null;

const isWindows = process.platform === "win32";
const result = isWindows
  ? spawnSync(
      process.env.ComSpec ?? "cmd.exe",
      ["/d", "/s", "/c", "pnpm --filter @eaos/shared gen"],
      { cwd: frontendDir, stdio: "inherit" },
    )
  : spawnSync("pnpm", ["--filter", "@eaos/shared", "gen"], {
      cwd: frontendDir,
      stdio: "inherit",
    });

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const after = readFileSync(generatedPath, "utf8");
if (before === after) {
  console.log("OpenAPI generated types are up to date.");
  process.exit(0);
}

if (existed && before !== null) {
  writeFileSync(generatedPath, before, "utf8");
} else {
  rmSync(generatedPath, { force: true });
}

console.error(
  "OpenAPI generated types are stale. Run `pnpm api:gen` and commit the result.",
);
process.exit(1);
