// App-wide state that outlives a page: the live camera session, and a preset
// handed from the Firmware page to the Presets page.
import { createSignal } from "solid-js";
import type { Camera, SerialLink } from "./serial";

export type Conn =
  | { state: "idle" }
  | { state: "opening" }
  | { state: "live"; link: SerialLink; camera: Camera }
  | { state: "lost"; reason: string };

export const [conn, setConn] = createSignal<Conn>({ state: "idle" });

export const [handoffPreset, setHandoffPreset] = createSignal<{ name: string; data: Uint8Array } | null>(null);
