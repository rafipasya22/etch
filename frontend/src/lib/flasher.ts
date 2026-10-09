import { ESPLoader, Transport } from "esptool-js";

export async function flashEsp32(
  port: SerialPort,
  url: string,
  log: (m: string) => void,
) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Firmware download failed (${res.status})`);
  const bytes = new Uint8Array(await res.arrayBuffer());

  const transport = new Transport(port, true);
  const loader = new ESPLoader({
    transport,
    baudrate: 460800,
    terminal: { clean() {}, writeLine: log, write: log },
  });

  let lastPct = -10;
  try {
    await loader.main();
    await loader.writeFlash({
      fileArray: [{ data: bytes, address: 0x0 }],
      flashSize: "keep",
      flashMode: "keep",
      flashFreq: "keep",
      eraseAll: false,
      compress: true,
      reportProgress: (_i: number, written: number, total: number) => {
        const pct = Math.floor((written / total) * 100);
        if (pct >= lastPct + 10) {
          lastPct = pct;
          log(`Flashing... ${pct}%`);
        }
      },
    });
    await loader.after();
  } finally {
    await transport.disconnect();
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function parseHex(text: string): Uint8Array {
  const buf = new Uint8Array(32768).fill(0xff);
  let base = 0;
  let max = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line.startsWith(":")) continue;
    const len = parseInt(line.substr(1, 2), 16);
    const addr = parseInt(line.substr(3, 4), 16);
    const type = parseInt(line.substr(7, 2), 16);
    if (type === 1) break;
    if (type === 2) base = parseInt(line.substr(9, 4), 16) * 16;
    else if (type === 4) base = parseInt(line.substr(9, 4), 16) * 65536;
    else if (type === 0) {
      for (let i = 0; i < len; i++) {
        const a = base + addr + i;
        if (a >= buf.length) throw new Error("Hex is too large for this board");
        buf[a] = parseInt(line.substr(9 + i * 2, 2), 16);
        if (a + 1 > max) max = a + 1;
      }
    }
  }
  return buf.slice(0, max);
}

async function stk500(
  port: SerialPort,
  baud: number,
  data: Uint8Array,
  log: (m: string) => void,
) {
  await port.open({ baudRate: baud });
  const reader = port.readable!.getReader();
  const writer = port.writable!.getWriter();

  const rx: number[] = [];
  let running = true;
  const pump = (async () => {
    try {
      while (running) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value) for (const b of value) rx.push(b);
      }
    } catch {}
  })();

  const expect = async (n: number, ms: number) => {
    const t = Date.now();
    while (rx.length < n) {
      if (Date.now() - t > ms) throw new Error("Timeout waiting for the board");
      await sleep(5);
    }
    return rx.splice(0, n);
  };

  const cmd = async (bytes: number[], ms = 1000) => {
    rx.length = 0;
    await writer.write(new Uint8Array(bytes));
    const r = await expect(2, ms);
    if (r[0] !== 0x14 || r[1] !== 0x10)
      throw new Error(`Unexpected reply: ${r.map((x) => x.toString(16)).join(" ")}`);
  };

  try {
    await port.setSignals({ dataTerminalReady: true, requestToSend: true });
    await sleep(250);
    await port.setSignals({ dataTerminalReady: false, requestToSend: false });
    await sleep(50);

    let synced = false;
    for (let i = 0; i < 10 && !synced; i++) {
      try {
        await cmd([0x30, 0x20], 300); 
        synced = true;
      } catch {}
    }
    if (!synced) throw new Error(`No bootloader response at ${baud} baud`);

    await cmd([0x50, 0x20]);

    const PAGE = 128; 
    let lastPct = -10;
    for (let off = 0; off < data.length; off += PAGE) {
      const chunk = data.slice(off, off + PAGE);
      const word = off >> 1;
      await cmd([0x55, word & 0xff, word >> 8, 0x20]); 
      await cmd([0x64, chunk.length >> 8, chunk.length & 0xff, 0x46, ...chunk, 0x20]); 
      const pct = Math.floor(((off + chunk.length) / data.length) * 100);
      if (pct >= lastPct + 10) {
        lastPct = pct;
        log(`Flashing... ${pct}%`);
      }
    }

    await cmd([0x51, 0x20]);
  } finally {
    running = false;
    try { await reader.cancel(); } catch {}
    await pump;
    reader.releaseLock();
    writer.releaseLock();
    try { await port.close(); } catch {}
  }
}

export async function flashAvr(
  port: SerialPort,
  fqbn: string,
  url: string,
  log: (m: string) => void,
) {
  const bauds =
    fqbn === "arduino:avr:uno" ? [115200]
    : fqbn === "arduino:avr:nano" ? [115200, 57600]
    : [];
  if (!bauds.length) throw new Error(`No AVR flasher for ${fqbn}`);

  const res = await fetch(url);
  if (!res.ok) throw new Error(`Firmware download failed (${res.status})`);
  const data = parseHex(await res.text());

  let lastErr: unknown;
  for (const baud of bauds) {
    try {
      log(`Connecting at ${baud} baud...`);
      await stk500(port, baud, data, log);
      log("Flash complete.");
      return;
    } catch (e) {
      lastErr = e;
      log(`Failed at ${baud}: ${e}`);
    }
  }
  throw lastErr;
}