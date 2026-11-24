// Dev-only fake camera: replaces navigator.serial with a port that answers like
// a healthy DSMC2 `dsh>` shell, so the Camera page (WebSerial transport, the
// wasm async driver, self-test, readings, terminal) can be exercised without
// hardware. Loaded only by the Vite dev server with `?mock-camera` in the URL;
// never part of a production build.
//
// Rails, temperatures and I2C devices come from the built-in reference body
// (the same golden the self-test checks against), so a self-test against this
// fake camera passes end to end.
import { default_golden } from "../wasm/dsmc_web.js";

const PROMPT = "(97%/97%) dsh>";

type Golden = {
  voltages: Record<string, [number, number]>;
  temps: Record<string, [number, number]>;
  i2c_present: string[];
};

// The wasm module is initialised by the time the first command arrives
// (Camera.open runs only after init), so the golden is read lazily.
let golden: Golden | null = null;
const ref = () => (golden ??= JSON.parse(default_golden()) as Golden);
const mid = ([lo, hi]: [number, number]) => (lo + hi) / 2;

// `?mock-camera=fault`: the first rail reads 10% under its window and the first
// I2C device stops answering, to exercise the failure display.
const FAULT = new URLSearchParams(location.search).get("mock-camera") === "fault";

function voltages(): string {
  const byBoard = new Map<string, string[]>();
  for (const [i, [key, win]] of Object.entries(ref().voltages).entries()) {
    const [board, rail] = key.split("/");
    // "Vcc 1.0" = rail name + nominal column; "Vdda_Ramp" has no nominal
    const [name, nominal = ""] = rail.split(" ");
    const v = FAULT && i === 0 ? win[0] * 0.9 : mid(win);
    const line = `  ${name.padEnd(16)} ${nominal.padEnd(6)} = ${v.toFixed(3)}`;
    byBoard.set(board, [...(byBoard.get(board) ?? []), line]);
  }
  return [...byBoard].map(([b, lines]) => `Checking - ${b}\n${lines.join("\n")}\n`).join("") + "Done\n";
}

function temperatures(): string {
  return Object.entries(ref().temps)
    .map(([sensor, win]) => `  ${sensor.padEnd(10)} = ${Math.round(mid(win))}  Pass\n`)
    .join("");
}

function i2cScan(bus: number): string {
  return ref()
    .i2c_present.slice(FAULT ? 1 : 0)
    .map((d) => d.match(/^bus(\d+):0x([0-9a-f]+)$/i))
    .filter((m): m is RegExpMatchArray => !!m && Number(m[1]) === bus)
    .map((m) => ` dev_${m[2].toLowerCase()}  : Addr = ${m[2].toUpperCase()}  - OK\n`)
    .join("");
}

const REPLIES: Record<string, string> = {
  "get system_state": "\nREADY_TO_RECORD (10)\n",
  "get isp_version": "ISP Version ID = D0\n",
  "get iob_version": "IOB Version ID = B0\n",
  "debug heartbeat isp1": "ISP1 heartbeat = 0x1A2B3C\n",
  "read sensor_dragon_reg 0": "Register Address 00000000: 000000E0\n",
  "test x12 status isp1 all":
    "Defibrillator - Pass\nRunning Check - Pass\nSer0 Lane 0 : Loss of Signal clear, Lane Error Count 0, Error bits None - Pass\nSer0 Lane 1 : Loss of Signal clear, Lane Error Count 0, Error bits None - Pass\n",
  "test x4 status isp-rx isp1 all": "Lane 0 : Signal Detect set, Error Count 0 - Pass\nLane 1 : Signal Detect set, Error Count 0 - Pass\n",
  "test x4 status iob-rx isp1 all": "Lane 0 : Signal Detect set, Error Count 0 - Pass\nLane 1 : Signal Detect set, Error Count 0 - Pass\n",
};

function reply(cmd: string): string {
  if (REPLIES[cmd] !== undefined) return REPLIES[cmd];
  if (cmd === "test voltage all") return voltages();
  if (cmd === "test temperature all") return temperatures();
  // UCD PMBus reads (STATUS_WORD / paged STATUS_VOUT): all clear
  if (cmd.startsWith("read i2c")) return `I2C READ data : ${Array(Number(cmd.split(" ").pop()) || 1).fill("00").join(" ")}\n`;
  if (cmd.startsWith("write i2c")) return "";
  if (cmd.startsWith("test i2c scan")) return i2cScan(Number(cmd.split(" ").pop()));
  if (cmd.startsWith("test ram")) return "Testing...\nPass\nDone\n";
  if (cmd === "help") return "read  write  nand  test  debug  get  set  ...\n";
  return `${cmd.split(" ")[0]}: unknown command\n`;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

class FakePort {
  readable: ReadableStream<Uint8Array> | null = null;
  writable: WritableStream<Uint8Array> | null = null;
  private ctl!: ReadableStreamDefaultController<Uint8Array>;
  private line = "";
  private enc = new TextEncoder();

  async open() {
    this.readable = new ReadableStream({ start: (c) => void (this.ctl = c) });
    this.writable = new WritableStream({ write: (chunk) => this.onWrite(new TextDecoder().decode(chunk)) });
    // boot chatter, then the banner prompt a moment later
    setTimeout(() => this.send("RED DSMC2 boot...\n"), 100);
  }

  private send(s: string) {
    try {
      this.ctl.enqueue(this.enc.encode(s));
    } catch {
      /* closed */
    }
  }

  private async onWrite(s: string) {
    for (const ch of s) {
      if (ch !== "\r") {
        this.line += ch;
        continue;
      }
      const cmd = this.line.trim();
      this.line = "";
      if (!cmd) {
        this.send(`\n${PROMPT}`);
        continue;
      }
      await sleep(40);
      const body = reply(cmd);
      const half = Math.floor(body.length / 2);
      this.send(`${cmd}\n${body.slice(0, half)}`);
      await sleep(30);
      this.send(`${body.slice(half)}${PROMPT}`);
    }
  }

  async close() {
    try {
      this.ctl.close();
    } catch {
      /* already closed */
    }
    this.readable = null;
    this.writable = null;
  }

  getInfo() {
    return { usbVendorId: 0x0403, usbProductId: 0x6001 };
  }
}

const serial = (navigator as Navigator & { serial?: object }).serial ?? {};
Object.defineProperty(navigator, "serial", { value: serial, configurable: true });
Object.assign(serial, { requestPort: async () => new FakePort() });
console.info(`[dsmc] mock camera installed: Connect opens a fake ${FAULT ? "faulty" : "healthy"} DSMC2 shell`);
export {};
