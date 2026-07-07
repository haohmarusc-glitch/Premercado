import { spawn } from "child_process";

const tickers = ["NVDA","SMCI","MU","INTC","GOOGL","ARM","TSLA","SNDK","WDC","ALAB","CRDO","ANET","VRT","TSM","ASML","SGOV","LLY","UNH","JNJ","ABBV","MRK","PFE","RADL3.SA","HAPV3.SA","RDOR3.SA","FLRY3.SA","ONCO3.SA","HCC"];
const scriptPath = "artifacts/api-server/src/agent/get_technicals.py";

const py = spawn("python3", [scriptPath]);
py.stdin.write(JSON.stringify({ tickers }));
py.stdin.end();
let out = "";
let err = "";
py.stdout.on("data", (d) => { out += d.toString(); });
py.stderr.on("data", (d) => { err += d.toString(); });
const t = setTimeout(() => { py.kill("SIGTERM"); console.log("TIMEOUT"); }, 90_000);
py.on("close", (code) => {
  clearTimeout(t);
  console.log("=== CODE ===", code);
  console.log("=== STDERR (" + err.length + " chars) ===");
  console.log(err.slice(0, 3000));
  console.log("=== STDOUT (" + out.length + " chars, first 300) ===");
  console.log(out.slice(0, 300));
  if (code === 0) {
    try {
      const parsed = JSON.parse(out);
      console.log("=== JSON.parse: OK, items:", parsed.items?.length, "===");
    } catch (e) {
      console.log("=== JSON.parse FAILED:", e.message, "===");
    }
  }
});
