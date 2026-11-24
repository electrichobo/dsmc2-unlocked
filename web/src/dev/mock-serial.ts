// Dev-only fake camera: replaces navigator.serial with a port that answers like
// a healthy DSMC2 `dsh>` shell, so the Camera page (WebSerial transport, the
// wasm async driver, self-test, readings, terminal) can be exercised without
// hardware. Loaded only by the Vite dev server with `?mock-camera` in the URL;
// never part of a production build.

const PROMPT = "(97%/97%) dsh>";

const REPLIES: Record<string, string> = {
  "get system_state": "\nREADY_TO_RECORD (10)\n",
  "get isp_version": "ISP Version ID = D0\n",
  "get iob_version": "IOB Version ID = B0\n",
  "debug heartbeat isp1": "ISP1 heartbeat = 0x1A2B3C\n",
  "read sensor_dragon_reg 0": "Register Address 00000000: 000000E0\n",
  "test voltage all":
    "Checking - IOB\n  Vcc       1.0    = 1.006\n  Vio_DV    1.8    = 1.805\nChecking - ISP\n  Vcc_Isp   0.9    = 0.914\n  Vdda_Ramp        = 1.90063\nDone\n",
  "test temperature all": "  IOB        = 51  Pass\n  Img Sensor = 39  Pass\n  FPGA       = 61  Pass\n",
  "test x12 status isp1 all":
    "Defibrillator - Pass\nRunning Check - Pass\nSer0 Lane 0 : Loss of Signal clear, Lane Error Count 0, Error bits None - Pass\nSer0 Lane 1 : Loss of Signal clear, Lane Error Count 0, Error bits None - Pass\n",
  "test x4 status isp-rx isp1 all": "Lane 0 : Signal Detect set, Error Count 0 - Pass\nLane 1 : Signal Detect set, Error Count 0 - Pass\n",
  "test x4 status iob-rx isp1 all": "Lane 0 : Signal Detect set, Error Count 0 - Pass\nLane 1 : Signal Detect set, Error Count 0 - Pass\n",
};

function reply(cmd: string): string {
  if (REPLIES[cmd] !== undefined) return REPLIES[cmd];
  if (cmd.startsWith("read i2c")) return "Data: 0x00 0x00\n";
  if (cmd.startsWith("test i2c scan")) return "";
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
console.info("[dsmc] mock camera installed: Connect opens a fake healthy DSMC2 shell");
export {};
