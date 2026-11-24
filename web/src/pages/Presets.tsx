import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Busy, DropFrame, Notice, PageHead, useTask } from "../components/ui";
import { readFile, saveBytes } from "../download";
import { doc } from "../links";
import { handoffPreset, setHandoffPreset } from "../state";
import { tools } from "../tools";

type Row = { id: number; name: string; value: string };
let nextId = 1;
const rows = (pairs: [string, string][]): Row[] => pairs.map(([name, value]) => ({ id: nextId++, name, value }));

const SHELL: [string, string][] = [
  ["DEBUG_ENABLE_SHELL", "1"],
  ["COMM_SERIAL_PORT_PROTOCOL", "4"],
];

export default function Presets() {
  const t = useTask();
  const [file, setFile] = createSignal<{ name: string; size: number } | null>(null);
  const [name, setName] = createSignal("shell.preset");
  const [version, setVersion] = createSignal(7);
  const [params, setParams] = createSignal<Row[] | null>(null);
  const [filter, setFilter] = createSignal("");

  async function load(fileName: string, data: Uint8Array) {
    setFile({ name: fileName, size: data.length });
    const p = await t.run("Decrypting the preset", () => tools.presetRead(data));
    if (p) {
      setName(fileName);
      setVersion(p.version);
      setParams(rows(p.params));
      setFilter("");
    }
  }

  onMount(() => {
    const h = handoffPreset();
    if (h) {
      setHandoffPreset(null);
      load(h.name, h.data);
    }
  });

  const start = (pairs: [string, string][], fileName: string) => {
    t.setError(null);
    setFile(null);
    setName(fileName);
    setVersion(7);
    setParams(rows(pairs));
  };

  const update = (id: number, patch: Partial<Row>) => setParams((ps) => ps!.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const problem = createMemo(() => {
    const ps = params();
    if (!ps) return null;
    const seen = new Set<string>();
    for (const r of ps) {
      const n = r.name.trim();
      if (!n) return "Every parameter needs a name.";
      if (seen.has(n)) return `${n} appears twice. Each name can be set once.`;
      seen.add(n);
    }
    if (!Number.isInteger(version())) return "The version must be a whole number.";
    return null;
  });

  const shown = createMemo(() => {
    const q = filter().toLowerCase();
    const ps = params() ?? [];
    return q ? ps.filter((r) => r.name.toLowerCase().includes(q) || r.value.toLowerCase().includes(q)) : ps;
  });

  async function build() {
    const ps = params();
    if (!ps || problem()) return;
    const pairs = ps.map((r) => [r.name.trim(), r.value] as [string, string]);
    const out = await t.run("Building the preset", () => tools.presetBuild(version(), pairs));
    if (out) saveBytes(name().endsWith(".preset") ? name() : `${name()}.preset`, out);
  }

  return (
    <div class="page">
      <PageHead title="Presets">
        A <code>.preset</code> is an encrypted settings database, the same format on DSMC2 and DSMC1 EPIC and SCARLET.
        When the camera boots with one in <code>/force_preset/</code> on a MINI-MAG, it writes every parameter into its
        settings, then deletes the file.
      </PageHead>

      <DropFrame title="Drop a .preset to read or edit it" hint="Or start from scratch below" accept=".preset" file={file()} onFile={async (f) => load(f.name, await readFile(f))} />

      <div class="row">
        <button class="btn" onClick={() => start(SHELL, "shell.preset")}>
          Start the debug shell preset
        </button>
        <button class="btn" onClick={() => start([["", ""]], "custom.preset")}>
          Start an empty preset
        </button>
      </div>
      <p class="muted small">
        The debug shell preset sets <code>DEBUG_ENABLE_SHELL=1</code> and routes the CTRL port to the shell. You need it
        before the Camera page can connect. <a href={doc("docs/access/serial-shell.md")}>How the shell is gated</a>.
      </p>

      <Show when={t.busy()}>
        <Busy>{t.busy()}</Busy>
      </Show>
      <Show when={t.error()}>
        <Notice>{t.error()}</Notice>
      </Show>

      <Show when={params()}>
        {(ps) => (
          <section class="section">
            <div class="spread">
              <h2>{ps().length} parameters</h2>
              <div class="row">
                <label class="check">
                  Format version
                  <input type="number" value={version()} min="1" style={{ width: "5em" }} onInput={(e) => setVersion(e.currentTarget.valueAsNumber)} />
                </label>
              </div>
            </div>
            <Show when={ps().length > 12}>
              <input type="search" placeholder="Filter parameters" value={filter()} onInput={(e) => setFilter(e.currentTarget.value)} aria-label="Filter parameters" />
            </Show>
            <div class="table-wrap scroll-y">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Value</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  <For each={shown()}>
                    {(r) => (
                      <tr>
                        <td>
                          <input class="mono" type="text" value={r.name} style={{ width: "100%" }} aria-label="Parameter name" onChange={(e) => update(r.id, { name: e.currentTarget.value })} />
                        </td>
                        <td>
                          <input class="mono" type="text" value={r.value} style={{ width: "100%" }} aria-label={`Value of ${r.name}`} onChange={(e) => update(r.id, { value: e.currentTarget.value })} />
                        </td>
                        <td class="num">
                          <button class="btn small" aria-label={`Remove ${r.name}`} onClick={() => setParams((p) => p!.filter((x) => x.id !== r.id))}>
                            Remove
                          </button>
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </div>
            <div class="row">
              <button class="btn" onClick={() => (setFilter(""), setParams((p) => [...p!, { id: nextId++, name: "", value: "" }]))}>
                Add parameter
              </button>
            </div>
            <p class="muted small">Whole numbers are stored as integers, everything else as text, as RED's own presets do.</p>
            <Show when={problem()}>
              <Notice kind="warn">{problem()}</Notice>
            </Show>
            <div class="row">
              <input class="filename" type="text" value={name()} onInput={(e) => setName(e.currentTarget.value)} aria-label="File name" />
              <button class="btn primary" disabled={!!problem() || !!t.busy()} onClick={build}>
                Build and download
              </button>
            </div>
          </section>
        )}
      </Show>
    </div>
  );
}
