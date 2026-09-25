// Run the production standalone server locally/CI the same way the Docker image does.
// Next's standalone output does not include static assets; copy them in first.
import { spawn } from "node:child_process";
import { cpSync, existsSync } from "node:fs";

const standalone = ".next/standalone";
if (!existsSync(`${standalone}/server.js`)) {
  console.error("No standalone build found. Run `pnpm build` first.");
  process.exit(1);
}
cpSync(".next/static", `${standalone}/.next/static`, { recursive: true });
if (existsSync("public")) cpSync("public", `${standalone}/public`, { recursive: true });

const server = spawn(process.execPath, ["server.js"], { cwd: standalone, stdio: "inherit" });
server.on("exit", (code) => process.exit(code ?? 0));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.kill(signal));
