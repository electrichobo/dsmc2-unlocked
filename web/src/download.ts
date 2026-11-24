import { unzipSync, zipSync } from "fflate";

export function saveBytes(name: string, data: Uint8Array | string, type = "application/octet-stream") {
  const blob = new Blob([data as BlobPart], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Zip `{path: bytes}` and save it. Stored, not deflated: FW payloads are already compressed. */
export function saveZip(name: string, files: Record<string, Uint8Array>) {
  saveBytes(name, zipSync(files, { level: 0 }), "application/zip");
}

export async function readFile(f: File): Promise<Uint8Array> {
  return new Uint8Array(await f.arrayBuffer());
}

const isZip = (d: Uint8Array) => d[0] === 0x50 && d[1] === 0x4b && d[2] === 0x03 && d[3] === 0x04;

const isDsmc = (d: Uint8Array) => d[0] === 0x44 && d[1] === 0x53 && d[2] === 0x4d && d[3] === 0x43;

export type FirmwareUpload = { kind: "dsmc" | "redone"; name: string; data: Uint8Array };

/**
 * RED ships firmware as a zip: DSMC bodies as `…/force_upgrade/<name>.bin`,
 * RED ONE as `…/upgrade/redone.su`. Accept the zip or the bare file and return
 * which kind it is plus the bytes to unpack.
 */
export function firmwareFromUpload(name: string, data: Uint8Array): FirmwareUpload {
  if (!isZip(data)) {
    if (isDsmc(data)) return { kind: "dsmc", name, data };
    if (/\.su$/i.test(name)) return { kind: "redone", name, data };
    throw new Error("This is not a RED firmware file. Expected a DSMC .bin, a RED ONE redone.su, or the zip either one came in.");
  }
  const entries = unzipSync(data, { filter: (f) => !f.name.startsWith("__MACOSX/") && /\.(bin|su|tar)$/i.test(f.name) });
  const files = Object.entries(entries);
  const dsmc = files.find(([p, d]) => /\.bin$/i.test(p) && isDsmc(d));
  if (dsmc) return { kind: "dsmc", name: dsmc[0].split("/").pop()!, data: dsmc[1] };
  const su = files.find(([p]) => /(^|\/)redone\.su$/i.test(p));
  if (su) return { kind: "redone", name: "redone.su", data: su[1] };
  if (files.some(([p]) => /(^|\/)su\.tar$/i.test(p)))
    throw new Error("This is an early RED ONE build (before build 16). Its su.tar is not encrypted: open it with any archive tool.");
  throw new Error("This zip has no RED firmware in it. Expected a DSMC .bin under force_upgrade/ or a RED ONE redone.su under upgrade/.");
}

export const fmtBytes = (n: number) =>
  n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KB` : `${(n / (1 << 20)).toFixed(1)} MB`;

export const today = () => new Date().toISOString().slice(0, 10);
