// PreToolUse guard: blocks commands that have burned this project before. Exit 2 = block, message goes back to Claude.
let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let input = {};
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
  }
  const tool = input.tool_name || "";
  const ti = input.tool_input || {};

  const block = (why) => {
    process.stderr.write(`Blocked by project guard: ${why}\n`);
    process.exit(2);
  };

  if (tool === "Bash") {
    const cmd = String(ti.command || "");
    if (/gcloud\s+builds\s+submit/.test(cmd))
      block("never deploy with `gcloud builds submit` (skips the test gate and ships untracked files). Merge to main instead.");
    if (/git\s+push\b[^\n|;&]*(\s--force\b|\s-f\b|\s\+\S)/.test(cmd))
      block("no force-push. Ask the owner first.");
    if (/--no-verify\b/.test(cmd)) block("do not skip hooks (--no-verify).");
    if (/git\s+add\b[^\n|;&]*(^|\s)\.env(\s|$)/.test(cmd)) block("never stage .env.");
    if (/gcloud\b[^\n]*(create|deploy|update)[^\n]*asia-south1|--region[= ]asia-south1/.test(cmd))
      block("asia-south1 (Mumbai) is deleted; use asia-southeast1 only.");
  }

  if (tool === "Write" || tool === "Edit") {
    const p = String(ti.file_path || "").replace(/\\/g, "/");
    if (/(^|\/)\.env(\.local|\.production)?$/.test(p)) block("do not edit .env files; tell the owner what to change.");
  }

  process.exit(0);
});
