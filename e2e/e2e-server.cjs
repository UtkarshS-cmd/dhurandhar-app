// E2E webServer launcher (used by playwright.config.cjs). Cross-platform Node
// wrapper so the same config runs on Windows locally and Linux in CI — the old
// .cmd launcher only worked on Windows. Spawns uvicorn from backend/ with the
// environment that playwright.config.cjs supplies (isolated SQLite DB, mock
// payments, RATE_LIMIT_ENABLED=false, explicit CORS).
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const BACKEND_DIR = path.join(__dirname, '..', 'backend');
const PORT = process.env.E2E_PORT || '5077';

function resolvePython() {
  // CI sets E2E_PYTHON to the runner's interpreter (no venv on disk there).
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const candidates = process.platform === 'win32'
    ? [path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')]
    : [path.join(BACKEND_DIR, '.venv', 'bin', 'python'), path.join(BACKEND_DIR, '.venv', 'bin', 'python3')];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

const child = spawn(
  resolvePython(),
  ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(PORT)],
  { cwd: BACKEND_DIR, stdio: 'inherit', env: process.env },
);

// Playwright stops the webServer by signaling this wrapper; forward the signal
// so uvicorn never orphans and keeps the port held (fatal for CI reruns).
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => { child.kill(signal); });
}
process.on('exit', () => { child.kill(); });
child.on('exit', (code, signal) => {
  process.exit(signal ? 1 : (code ?? 0));
});
