import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
async function files(dir) {
  return (
    await Promise.all(
      (await readdir(dir, { withFileTypes: true })).map((entry) =>
        entry.isDirectory()
          ? files(join(dir, entry.name))
          : join(dir, entry.name),
      ),
    )
  ).flat();
}
let count = 0;
for (const path of await files(".next/static")) {
  if (!path.endsWith(".js")) continue;
  count++;
  const text = await readFile(path, "utf8");
  if (
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}|cv_live_[A-Za-z0-9]{24,}|127\.0\.0\.1:44999|localhost:8000|127\.0\.0\.1:8000/.test(
      text,
    )
  )
    throw new Error(
      `Secret or build-time deployment config in bundle: ${path}`,
    );
}
if (!count) throw new Error("No production JS chunks found");
console.log(
  `Production bundle scan PASS (${count} JS chunks; bounded credential/config patterns, not a universal secret guarantee)`,
);
