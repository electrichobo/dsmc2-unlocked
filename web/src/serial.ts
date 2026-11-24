// WebSerial link to the camera's USB serial shell, shaped as the wasm
// `JsTransport`: a background loop buffers everything the camera sends,
// `read()` drains that buffer without waiting, and every received chunk is
// also handed to listeners (the terminal view).
import init, { Camera } from "./wasm/dsmc_web.js";
import type { JsTransport } from "./wasm/dsmc_web.js";

export const BAUD = 115200;

export const serialSupported = () => typeof navigator !== "undefined" && "serial" in navigator;

let wasmReady: Promise<unknown> | null = null;
const loadWasm = () => (wasmReady ??= init());

export class SerialLink implements JsTransport {
  private pending: Uint8Array[] = [];
  private listeners = new Set<(chunk: Uint8Array) => void>();
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private loop: Promise<void> | null = null;
  private closing = false;
  onClose: ((reason: string) => void) | null = null;

  private constructor(readonly port: SerialPort) {}

  /** Ask the browser for a port (must run from a click) and open it 8N1. */
  static async request(): Promise<SerialLink> {
    const port = await navigator.serial.requestPort();
    await port.open({ baudRate: BAUD, dataBits: 8, parity: "none", stopBits: 1, flowControl: "none" });
    const link = new SerialLink(port);
    link.loop = link.readLoop();
    return link;
  }

  private async readLoop() {
    while (this.port.readable && !this.closing) {
      this.reader = this.port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          if (value?.length) {
            this.pending.push(value);
            for (const l of this.listeners) l(value);
          }
        }
      } catch (e) {
        if (!this.closing) {
          this.onClose?.(e instanceof Error ? e.message : String(e));
          return;
        }
      } finally {
        this.reader.releaseLock();
        this.reader = null;
      }
    }
    if (!this.closing) this.onClose?.("The camera disconnected.");
  }

  listen(fn: (chunk: Uint8Array) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  async write(data: Uint8Array): Promise<void> {
    const copy = data.slice();
    const w = this.port.writable!.getWriter();
    try {
      await w.write(copy);
    } finally {
      w.releaseLock();
    }
  }

  async read(): Promise<Uint8Array> {
    if (!this.pending.length) return new Uint8Array();
    const total = this.pending.reduce((n, c) => n + c.length, 0);
    const out = new Uint8Array(total);
    let o = 0;
    for (const c of this.pending) {
      out.set(c, o);
      o += c.length;
    }
    this.pending = [];
    return out;
  }

  async clear(): Promise<void> {
    this.pending = [];
  }

  sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
  }

  async close() {
    this.closing = true;
    await this.reader?.cancel().catch(() => {});
    await this.loop;
    await this.port.close().catch(() => {});
  }
}

/** Open the `dsh>` session on a link: waits for the prompt (pokes CR). */
export async function openCamera(link: SerialLink, promptTimeoutMs = 15000): Promise<Camera> {
  await loadWasm();
  return (await Camera.open(link, promptTimeoutMs)) as Camera;
}

export { loadWasm };
export type { Camera };
