import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const bridge = fileURLToPath(new URL('../bin/hermes-mcp-jsonl-bridge.mjs', import.meta.url));

test('converts framed MCP output to JSONL without changing input', () => {
  const server = `
    const fs = require('node:fs');
    for (const line of fs.readFileSync(0, 'utf8').trim().split('\\n')) {
      const request = JSON.parse(line);
      const body = JSON.stringify({jsonrpc:'2.0', id:request.id, result:'Ω'});
      process.stdout.write('Content-Length: ' + Buffer.byteLength(body) + '\\r\\n\\r\\n' + body);
    }
  `;
  const requests = [1, 2].map(id => JSON.stringify({ jsonrpc: '2.0', id, method: 'tools/list' })).join('\n') + '\n';
  const result = spawnSync(process.execPath, [bridge, process.execPath, '-e', server],
                           { input: requests, encoding: 'utf8', timeout: 3000 });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trim().split('\n').map(JSON.parse), [1, 2].map(id =>
    ({ jsonrpc: '2.0', id, result: 'Ω' })));
});

test('rejects malformed server framing', () => {
  const result = spawnSync(process.execPath, [bridge, process.execPath, '-e',
    "process.stdout.write('not a frame')"], { encoding: 'utf8', timeout: 3000 });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /incomplete MCP frame/);
});
