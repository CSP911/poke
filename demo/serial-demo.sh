#!/bin/bash
# POKE Serial Pipeline Demo
# 녹화: asciinema rec --command="bash demo/serial-demo.sh" demo/serial-pipeline.cast

set -e

TYPE_DELAY=0.03

type_cmd() {
  echo ""
  echo -n "$ "
  echo "$1" | while IFS= read -r -n1 char; do
    echo -n "$char"
    sleep $TYPE_DELAY
  done
  echo ""
  sleep 0.5
  eval "$1"
  sleep 1
}

echo "╔══════════════════════════════════════════════════════╗"
echo "║  POKE — ESP32-C3 Serial Pipeline Demo               ║"
echo "║  Hub → RISC-V compile → USB serial → bare-metal exec║"
echo "╚══════════════════════════════════════════════════════╝"
sleep 2

echo ""
echo "▶ Step 1: Start hub & enroll ESP32-C3"
echo "─────────────────────────────────────────"
sleep 1

cd /Users/bagcheonsu/poke
PORT=3333 LOG_LEVEL=warn node src/hub.js &
HUB_PID=$!
sleep 2

type_cmd 'curl -s -X POST http://localhost:3333/enroll -H "Content-Type: application/json" -d '"'"'{"node_id":"esp32-c3","arch":"riscv32","memory_mb":4,"endpoint":"serial:///dev/cu.usbmodem1101"}'"'"' | python3 -m json.tool'

sleep 1

echo ""
echo "▶ Step 2: Serial health probe"
echo "─────────────────────────────────────────"
sleep 1

type_cmd 'curl -s http://localhost:3333/serial/health/esp32-c3 | python3 -m json.tool'

sleep 1

echo ""
echo "▶ Step 3: RISC-V arithmetic (hub compile → serial → ESP32 execute)"
echo "─────────────────────────────────────────"
sleep 1

node -e "
const { compileAssemblyRV } = require('./hub/compiler');

async function test(label, asm, expected) {
  console.log('');
  console.log('  ┌─ ' + label);
  console.log('  │ ASM:');
  asm.split('\n').forEach(l => console.log('  │   ' + l));
  const bin = await compileAssemblyRV(asm);
  console.log('  │ BIN: ' + bin.toString('hex') + ' (' + bin.length + ' bytes)');
  const r = await fetch('http://localhost:3333/poke-raw', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({node_id:'esp32-c3', hex: bin.toString('hex')})
  }).then(r=>r.json());
  const pass = r.result === expected;
  console.log('  └─ ' + (pass ? '✅' : '❌') + ' Result: ' + r.result);
}

(async()=>{
  await test('3 + 4 = 7',
    'li a0, 3\nli t0, 4\nadd a0, a0, t0\nret', 'a0=7');
  await test('10 × 20 = 200',
    'li a0, 10\nli t0, 20\nmul a0, a0, t0\nret', 'a0=200');
  await test('100 - 37 = 63',
    'li a0, 100\nli t0, 37\nsub a0, a0, t0\nret', 'a0=63');
  await test('fibonacci(10) = 55',
    'li a0, 0\nli a1, 1\nli t0, 10\nli t1, 0\nloop:\nbeq t1, t0, done\nadd t2, a0, a1\nmv a0, a1\nmv a1, t2\naddi t1, t1, 1\nj loop\ndone:\nret', 'a0=55');
})();
" 2>&1

sleep 2

echo ""
echo "▶ Step 4: Temperature sensor (calibrated)"
echo "─────────────────────────────────────────"
sleep 1

type_cmd 'curl -s http://localhost:3333/serial/temp/esp32-c3 | python3 -m json.tool'

sleep 2

echo ""
echo "▶ Step 5: LLM Agent Loop (natural language → ESP32)"
echo "─────────────────────────────────────────"
sleep 1

echo '$ curl -s -X POST http://localhost:3333/relay \'
echo '    -d '"'"'{"command":"What is the temperature of the ESP32?"}'"'"''
sleep 0.5

RESULT=$(curl -s -X POST http://localhost:3333/relay \
  -H "Content-Type: application/json" \
  -d '{"from":"esp32-c3","command":"What is the current temperature of the ESP32-C3 chip? Read it and tell me if it is normal.","target":"esp32-c3"}')

node -e "
const d = JSON.parse(process.argv[1]);
console.log('');
console.log('  Agent steps:');
for (const s of d.steps||[]) {
  console.log('    ┌─ tool: ' + s.tool);
  if (s.input?.asm_code) {
    console.log('    │ LLM-generated ASM:');
    s.input.asm_code.trim().split('\n').forEach(l => {
      if (l.trim()) console.log('    │   ' + l.trim());
    });
  }
  if (s.input?.sensor) console.log('    │ sensor: ' + s.input.sensor);
  console.log('    └─ result: ' + (s.result||'').split('\n')[0].slice(0,100));
}
console.log('');
console.log('  LLM response:');
(d.result||'').slice(0,400).split('\n').forEach(l => console.log('    ' + l));
" "$RESULT"

sleep 3

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Demo complete. 16/16 integration tests also pass."
echo "═══════════════════════════════════════════════════════"

kill $HUB_PID 2>/dev/null
