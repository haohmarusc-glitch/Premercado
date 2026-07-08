import { spawn } from "child_process";
import fs from "fs";

const tickers = ["NVDA","SMCI","MU","INTC","GOOGL","ARM","TSLA","SNDK","WDC","ALAB","CRDO","ANET","VRT","TSM","ASML","SGOV","LLY","UNH","JNJ","ABBV","MRK","PFE","RADL3.SA","HAPV3.SA","RDOR3.SA","FLRY3.SA","ONCO3.SA","HCC"];
const scriptPath = "artifacts/api-server/src/agent/get_technicals.py";

console.log("=== file mtime ===", fs.statSync(scriptPath).mtime);
console.log("=== file contains new RSI guard? ===", fs.readFileSync(scriptPath, "utf8").includes("avg_loss_last == 0"));

const py = spawn("python3", [scriptPath]);
py.stdin.write(JSON.stringify({ tickers }));
py.stdin.end();
let out = "";
let err = "";
py.stdout.on("data", (d) => { out += d.toString(); });
py.stderr.on("data", (d) => { err += d.toString(); });
py.on("close", (code) => {
  console.log("=== CODE ===", code);
  console.log("=== STDERR (" + err.length + " chars) ===");
  console.log(err.slice(0, 2000));
  console.log("=== STDOUT length ===", out.length);
  const nanMatch = out.match(/[:,]\s*NaN\b/);
  console.log("=== has bare NaN token? ===", !!nanMatch, nanMatch ? out.slice(Math.max(0,nanMatch.index-60), nanMatch.index+30) : "");
  try {
    JSON.parse(out);
    console.log("=== JSON.parse: OK ===");
  } catch (e) {
    console.log("=== JSON.parse FAILED:", e.message, "===");
    console.log(out.slice(0, 200));
  }
});
