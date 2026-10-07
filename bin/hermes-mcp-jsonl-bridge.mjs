#!/usr/bin/env node
import { spawn } from 'node:child_process';

if (process.argv.length < 3) {
  console.error('usage: hermes-mcp-jsonl-bridge.mjs <mcp-server> [args...]');
  process.exit(2);
}

const child = spawn(process.argv[2], process.argv.slice(3), { stdio: ['inherit', 'pipe', 'inherit'] });
let buffer = Buffer.alloc(0);
child.stdout.on('data', chunk => {
  buffer = Buffer.concat([buffer, chunk]);
  while (buffer.length) {
    const end = buffer.indexOf('\r\n\r\n');
    if (end < 0 && buffer.length <= 8192) return;
    const header = end < 0 ? '' : buffer.subarray(0, end).toString('ascii');
    // ZBrain 0.8.0 emits one Content-Length header; reject transport drift.
    const length = /^Content-Length: (\d+)$/.exec(header);
    if (!length || Number(length[1]) > 8 * 1024 * 1024) {
      console.error('unexpected MCP frame from server');
      process.exitCode = 1;
      child.kill();
      return;
    }
    const start = end + 4;
    if (buffer.length < start + Number(length[1])) return;
    process.stdout.write(buffer.subarray(start, start + Number(length[1])).toString('utf8') + '\n');
    buffer = buffer.subarray(start + Number(length[1]));
  }
});
child.on('error', error => { console.error(error.message); process.exitCode = 1; });
child.on('close', code => {
  if (buffer.length) { console.error('incomplete MCP frame from server'); process.exitCode = 1; }
  if (code !== 0) process.exitCode = code || 1;
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
