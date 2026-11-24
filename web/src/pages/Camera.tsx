import { A } from "@solidjs/router";
import { createMemo, createSignal, For, JSX, Match, onCleanup, Show, Switch } from "solid-js";
import { Busy, DropFrame, Notice, PageHead, useTask } from "../components/ui";
import { readFile, saveBytes, today } from "../download";
import { doc } from "../links";
import { loadWasm, openCamera, SerialLink, serialSupported, type Camera } from "../serial";
import { conn, setConn } from "../state";
import type { I2cV, LaneV, RailV, Selftest, SignalCheck, TempV, UcdV } from "../types";
import * as wasm from "../wasm/dsmc_web.js";

const [camBusy, setCamBusy] = createSignal<string | null>(null);

async function connect(setError: (e: string | null) => void) {
  setError(null);
  let link: SerialLink;
  try {
    link = await SerialLink.request();
  } catch (e) {
    // NotFoundError = the person closed the port picker; not an error to show.
    if (!(e instanceof DOMException && e.name === "NotFoundError")) setError(e instanceof Error ? e.message : String(e));
    return;
  }
  setConn({ state: "opening" });
  link.onClose = (reason) => setConn({ state: "lost", reason });
  try {
    const camera = await openCamera(link);
    setConn({ state: "live", link, camera });
  } catch (e) {
    await link.close();
    setConn({ state: "idle" });
    setError(e instanceof Error ? e.message : String(e));
  }
}

async function disconnect() {
  const c = conn();
  if (c.state === "live") {
    c.link.onClose = null;
    await c.link.close();
    c.camera.free();
  }
  setConn({ state: "idle" });
}

/** Run one camera call, marking the session busy so the terminal waits. */
async function onCamera<T>(label: string, f: (c: Camera) => Promise<T>): Promise<T> {
  const c = conn();
  if (c.state !== "live") throw new Error("No camera connected.");
  setCamBusy(label);
  try {
    return await f(c.camera);
  } finally {
    setCamBusy(null);
  }
}

export default function CameraPage() {
  const [err, setErr] = createSignal<string | null>(null);
  return (
    <div class="page">
      <PageHead title="Camera">
        Connect to the camera's debug shell over the CTRL port and check its health. The self-test only reads the
        camera. The terminal sends whatever you type.
      </PageHead>

      <Show
        when={serialSupported()}
        fallback={
          <Notice kind="warn">
            This browser cannot open serial ports. Use Chrome or Edge on a desktop computer, or the{" "}
            <a href={doc("tools/dsmcdbg/README.md")}>dsmcdbg</a> command-line tool, which runs the same checks.
          </Notice>
        }
      >
        <Switch>
          <Match when={conn().state === "idle" || conn().state === "lost"}>
            <Show when={conn().state === "lost" && (conn() as { reason: string })}>
              {(c) => <Notice kind="warn">The connection closed: {c().reason}</Notice>}
            </Show>
            <ol class="steps">
              <li>
                <div>
                  <h3>Turn on the shell</h3>
                  <p>
                    Build the debug shell preset on the <A href="/presets">Presets</A> page, copy it to{" "}
                    <code>/force_preset/</code> on a MINI-MAG, and boot the camera once with it inserted.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <h3>Wire the CTRL port</h3>
                  <p>
                    CTRL is RS-232, so it needs a MAX3232 powered from 5 V between the camera and a USB serial adapter
                    (FT232, CP2102 or RP2040). A bare USB adapter will not work.{" "}
                    <a href={doc("docs/hardware-fw/serial-connection-options.md")}>Wiring details</a>.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <h3>Connect</h3>
                  <p>Power the camera on, then choose your USB serial adapter. The link runs at 115200 8N1.</p>
                </div>
              </li>
            </ol>
            <div class="row">
              <button class="btn primary" onClick={() => connect(setErr)}>
                Connect to camera
              </button>
            </div>
          </Match>
          <Match when={conn().state === "opening"}>
            <Busy>Waiting for the dsh&gt; prompt. The camera prints it a few seconds into boot.</Busy>
          </Match>
          <Match when={conn().state === "live"}>
            <Live />
          </Match>
        </Switch>
        <Show when={err()}>
          <Notice>{err()}</Notice>
        </Show>
      </Show>

      <SavedCapture />
    </div>
  );
}

function Live() {
  const [tab, setTab] = createSignal<"selftest" | "readings" | "terminal">("selftest");
  const tabs = [
    ["selftest", "Self-test"],
    ["readings", "Readings"],
    ["terminal", "Terminal"],
  ] as const;
  return (
    <div class="section">
      <div class="spread">
        <div class="live">
          <span class="tally on" />
          <strong>Camera connected</strong>
        </div>
        <button class="btn danger small" onClick={disconnect} disabled={!!camBusy()}>
          Disconnect
        </button>
      </div>
      <div class="tabs" role="tablist">
        <For each={tabs}>
          {([id, label]) => (
            <button role="tab" aria-selected={tab() === id} onClick={() => setTab(id)}>
              {label}
            </button>
          )}
        </For>
      </div>
      <div style={{ display: tab() === "selftest" ? "contents" : "none" }}>
        <SelftestPanel />
      </div>
      <div style={{ display: tab() === "readings" ? "contents" : "none" }}>
        <Readings />
      </div>
      <div style={{ display: tab() === "terminal" ? "contents" : "none" }}>
        <Terminal />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ self-test

function SelftestPanel() {
  const t = useTask();
  const [deep, setDeep] = createSignal(false);
  const [golden, setGolden] = createSignal<{ name: string; json: string } | null>(null);
  const [step, setStep] = createSignal("");
  const [res, setRes] = createSignal<Selftest | null>(null);

  async function run() {
    setRes(null);
    const r = await t.run("Running the self-test", () =>
      onCamera("self-test", (c) => c.selftest(golden()?.json ?? null, deep(), today(), (s: string) => setStep(s))),
    );
    if (r) setRes(r as Selftest);
  }

  return (
    <div class="section">
      <p class="muted">
        Reads system state, firmware versions, every voltage rail and temperature, the sensor X12 and X4 lanes, the UCD
        power sequencers and the I²C devices, then checks each one against a reference body. Takes about two minutes.
      </p>
      <div class="row">
        <label class="check">
          <input type="checkbox" checked={deep()} onChange={(e) => setDeep(e.currentTarget.checked)} />
          Include the DDR memory tests (adds several minutes)
        </label>
      </div>
      <p class="muted small">
        The built-in reference is a healthy DSMC2 SCARLET-W 5K. Other bodies, and DSMC1 bodies especially, have
        different rails and devices, so expect differences against it. For those, run the self-test on a body you know is
        good, download its capture, and compare later captures against it under Check a saved capture.
      </p>
      <div class="row">
        <span class="muted small">Reference: {golden()?.name ?? "built-in healthy SCARLET-W 5K"}</span>
        <label class="btn small">
          Use my own reference
          <input
            type="file"
            accept=".json"
            hidden
            onChange={async (e) => {
              const f = e.currentTarget.files?.[0];
              if (f) setGolden({ name: f.name, json: await f.text() });
            }}
          />
        </label>
        <Show when={golden()}>
          <button class="btn small" onClick={() => setGolden(null)}>
            Use the built-in reference
          </button>
        </Show>
      </div>
      <div class="row">
        <button class="btn primary" disabled={!!camBusy()} onClick={run}>
          Run self-test
        </button>
      </div>
      <Show when={t.busy()}>
        <Busy>Checking {step() || "the camera"}</Busy>
      </Show>
      <Show when={t.error()}>
        <Notice>{t.error()}</Notice>
      </Show>
      <Show when={res()}>{(r) => <SelftestResult res={r()} />}</Show>
    </div>
  );
}

const VERDICT: Record<string, [string, string]> = {
  Pass: ["ok", "Pass"],
  Fail: ["fail", "Fail"],
  ExpectedPass: ["info", "Known normal"],
  Info: ["info", "Info"],
};

function SelftestResult(props: { res: Selftest }) {
  const counts = createMemo(() => {
    const c = { Pass: 0, Fail: 0, ExpectedPass: 0, Info: 0 };
    for (const r of props.res.checks) c[r.verdict]++;
    return c;
  });
  const boards = createMemo(() => {
    const m = new Map<string, SignalCheck[]>();
    for (const r of props.res.checks) m.set(r.board, [...(m.get(r.board) ?? []), r]);
    return [...m];
  });
  return (
    <>
      <div class="verdict" classList={{ fail: props.res.faults > 0 }}>
        <div>
          <h2>{props.res.faults ? `${props.res.faults} signal${props.res.faults > 1 ? "s" : ""} out of range` : "Every checked signal is healthy"}</h2>
          <p>
            {counts().Pass} pass, {counts().Fail} fail, {counts().ExpectedPass} known normal, {counts().Info} informational.
            {props.res.faults ? " Each failing row names the board and what to check." : ""}
          </p>
        </div>
      </div>
      <div class="row">
        <button class="btn" onClick={() => saveBytes(`capture_${props.res.capture.captured}.json`, JSON.stringify(props.res.capture, null, 2), "application/json")}>
          Download capture
        </button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Signal</th>
              <th>Result</th>
              <th>Expected</th>
              <th>Measured</th>
              <th>Note</th>
            </tr>
          </thead>
          <For each={boards()}>
            {([board, rows]) => (
              <tbody>
                <tr>
                  <th colSpan={5}>{board}</th>
                </tr>
                <For each={rows}>
                  {(r) => (
                    <tr>
                      <td>{r.signal}</td>
                      <td>
                        <span class={`pill ${VERDICT[r.verdict][0]}`}>{VERDICT[r.verdict][1]}</span>
                      </td>
                      <td class="small">{r.golden}</td>
                      <td class="small">{r.measured}</td>
                      <td class="small">{r.note}</td>
                    </tr>
                  )}
                </For>
              </tbody>
            )}
          </For>
        </table>
      </div>
    </>
  );
}

type Deviation = { board: string; signal: string; golden: string; measured: string; repair: string };

/** Check a capture saved earlier (or by `dsmcdbg -o`) without a camera. */
function SavedCapture() {
  const t = useTask();
  const [file, setFile] = createSignal<File | null>(null);
  const [json, setJson] = createSignal<string | null>(null);
  const [res, setRes] = createSignal<Selftest | null>(null);
  const [ref, setRef] = createSignal<File | null>(null);
  const [diff, setDiff] = createSignal<Deviation[] | null>(null);

  const text = async (f: File) => new TextDecoder().decode(await readFile(f));

  return (
    <section class="section">
      <header>
        <h2>Check a saved capture</h2>
        <p>
          Open a capture downloaded here or written by <code>dsmcdbg selftest -o</code>. It is checked against the
          built-in reference, or compared with a capture from a known-good body. No camera needed.
        </p>
      </header>
      <DropFrame
        compact
        title="Drop a capture .json"
        hint="The capture to check"
        accept=".json"
        file={file()}
        onFile={async (f) => {
          setFile(f);
          setRes(null);
          setDiff(null);
          setRef(null);
          const j = await text(f);
          setJson(j);
          const r = await t.run("Checking", async () => {
            await loadWasm();
            return wasm.assert_capture(j) as Selftest;
          });
          if (r) setRes(r);
        }}
      />
      <Show when={json()}>
        <DropFrame
          compact
          title="Compare with a known-good capture"
          hint="Optional: a capture from a healthy body of the same model"
          accept=".json"
          file={ref()}
          onFile={async (f) => {
            setRef(f);
            const r = await t.run("Comparing", async () => wasm.diff_captures(await text(f), json()!) as Deviation[]);
            if (r) setDiff(r);
          }}
        />
      </Show>
      <Show when={t.error()}>
        <Notice>{t.error()}</Notice>
      </Show>
      <Show when={diff()}>
        {(d) => (
          <div class="section">
            <div class="verdict" classList={{ fail: d().length > 0 }}>
              <div>
                <h2>{d().length ? `${d().length} difference${d().length > 1 ? "s" : ""} from the known-good body` : "Matches the known-good body"}</h2>
                <p>Compared signal by signal with {ref()?.name}.</p>
              </div>
            </div>
            <Show when={d().length}>
              <Table
                head={["Board", "Signal", "Known good", "This body", "What to check"]}
                rows={d().map((x) => [x.board, x.signal, x.golden, x.measured, x.repair])}
              />
            </Show>
          </div>
        )}
      </Show>
      <Show when={res()}>{(r) => <SelftestResult res={r()} />}</Show>
    </section>
  );
}

// ------------------------------------------------------------------- readings

type Reading = { title: string; raw?: string; body: JSX.Element };

function Table(props: { head: string[]; rows: (string | JSX.Element)[][]; num?: number[] }) {
  return (
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <For each={props.head}>{(h, i) => <th classList={{ num: props.num?.includes(i()) }}>{h}</th>}</For>
          </tr>
        </thead>
        <tbody>
          <For each={props.rows}>
            {(r) => (
              <tr>
                <For each={r}>{(c, i) => <td classList={{ num: props.num?.includes(i()) }}>{c}</td>}</For>
              </tr>
            )}
          </For>
        </tbody>
      </table>
    </div>
  );
}

const passPill = (ok: boolean, yes = "Pass", no = "Fail") => <span class={`pill ${ok ? "ok" : "fail"}`}>{ok ? yes : no}</span>;

function lanes(l: LaneV): JSX.Element {
  return (
    <div class="panel">
      <div class="row">
        {passPill(l.all_pass, "All lanes clean", "Lane errors")}
        <span class="muted">{l.lanes} lanes read</span>
      </div>
      <Show when={l.failing_lanes.length}>
        <pre class="mono small">{l.failing_lanes.join("\n")}</pre>
      </Show>
    </div>
  );
}

const READINGS: { id: string; label: string; run: (c: Camera) => Promise<Reading> }[] = [
  {
    id: "state",
    label: "System state",
    run: async (c) => {
      const raw = (await c.run("get system_state", 8000, false)) as string;
      const s = wasm.parse_system_state(raw) as { name: string; code: number } | null;
      return {
        title: "System state",
        raw,
        body: s ? (
          <div class="row">
            {passPill(s.code === 10, s.name, s.name)}
            <span class="muted">state {s.code}{s.code === 10 ? ", ready to record" : ", not ready"}</span>
          </div>
        ) : (
          <p class="muted">The reply had no state line.</p>
        ),
      };
    },
  },
  {
    id: "volt",
    label: "Voltages",
    run: async (c) => {
      const raw = (await c.run("test voltage all", 15000, false)) as string;
      const v = wasm.parse_voltages(raw) as RailV[];
      return {
        title: "Voltage rails",
        raw,
        body: <Table head={["Board", "Rail", "Nominal (V)", "Measured (V)"]} num={[2, 3]} rows={v.map((r) => [r.board, r.name, r.nominal?.toFixed(2) ?? "", r.measured.toFixed(3)])} />,
      };
    },
  },
  {
    id: "temp",
    label: "Temperatures",
    run: async (c) => {
      const raw = (await c.run("test temperature all", 15000, false)) as string;
      const v = wasm.parse_temperatures(raw) as TempV[];
      return {
        title: "Temperatures",
        raw,
        body: <Table head={["Sensor", "°C", "Result"]} num={[1]} rows={v.map((r) => [r.sensor, String(r.celsius), passPill(r.pass)])} />,
      };
    },
  },
  {
    id: "x12",
    label: "X12 sensor lanes",
    run: async (c) => {
      const raw = (await c.run("test x12 status isp1 all", 25000, true)) as string;
      return { title: "X12 lanes (sensor to ISP)", raw, body: lanes(wasm.parse_lane_status(raw) as LaneV) };
    },
  },
  {
    id: "x4",
    label: "X4 lanes",
    run: async (c) => {
      const raw = (await c.run("test x4 status isp-rx isp1 all", 25000, true)) as string;
      return { title: "X4 lanes (ISP receive)", raw, body: lanes(wasm.parse_lane_status(raw) as LaneV) };
    },
  },
  {
    id: "hb",
    label: "ISP heartbeat",
    run: async (c) => {
      const raw = (await c.run("debug heartbeat isp1", 8000, false)) as string;
      return { title: "ISP heartbeat", raw, body: <p class="muted">A value that changes between reads means the ISP core is running.</p> };
    },
  },
  {
    id: "ucd",
    label: "Power sequencers",
    run: async (c) => {
      const v = (await c.ucd()) as UcdV[];
      return {
        title: "UCD power sequencers",
        body: <Table head={["Board", "Part", "Rails", "Notes"]} rows={v.map((r) => [r.board, r.part, passPill(r.status_vout_all_ok, "OK", "Fault"), r.flags.join("; ")])} />,
      };
    },
  },
  {
    id: "i2c",
    label: "I²C scan",
    run: async (c) => {
      const v = (await c.i2c_scan()) as I2cV[];
      return {
        title: "I²C devices",
        body: <Table head={["Bus", "Address", "Device", "Scan"]} rows={v.map((r) => [String(r.bus), "0x" + r.addr.toString(16).padStart(2, "0"), r.label, r.scan])} />,
      };
    },
  },
];

function Readings() {
  const t = useTask();
  const [out, setOut] = createSignal<Reading | null>(null);
  return (
    <div class="section">
      <p class="muted">Each reading runs one read-only shell command and shows the parsed result.</p>
      <div class="row">
        <For each={READINGS}>
          {(r) => (
            <button
              class="btn"
              disabled={!!camBusy()}
              onClick={async () => {
                setOut(null);
                const v = await t.run(`Reading ${r.label.toLowerCase()}`, () => onCamera(r.label, r.run));
                if (v) setOut(v);
              }}
            >
              {r.label}
            </button>
          )}
        </For>
      </div>
      <Show when={t.busy()}>
        <Busy>{t.busy()}</Busy>
      </Show>
      <Show when={t.error()}>
        <Notice>{t.error()}</Notice>
      </Show>
      <Show when={out()}>
        {(o) => (
          <div class="section">
            <h3>{o().title}</h3>
            {o().body}
            <Show when={o().raw}>
              <details>
                <summary class="muted small">Raw shell reply</summary>
                <pre class="term" style={{ height: "auto", "max-height": "320px" }}>
                  {o().raw}
                </pre>
              </details>
            </Show>
          </div>
        )}
      </Show>
    </div>
  );
}

// ------------------------------------------------------------------- terminal

const MAX_TERM = 200_000;

function Terminal() {
  const [text, setText] = createSignal("");
  const [line, setLine] = createSignal("");
  const [history, setHistory] = createSignal<string[]>([]);
  let hi = -1;
  let pane!: HTMLPreElement;
  const dec = new TextDecoder();
  const c = conn();
  if (c.state === "live") {
    const off = c.link.listen((chunk) => {
      const atBottom = pane && pane.scrollHeight - pane.scrollTop - pane.clientHeight < 24;
      setText((t) => (t + dec.decode(chunk, { stream: true }).replace(/\r\n?/g, "\n")).slice(-MAX_TERM));
      if (atBottom) queueMicrotask(() => (pane.scrollTop = pane.scrollHeight));
    });
    onCleanup(off);
  }

  async function send(e: Event) {
    e.preventDefault();
    const c = conn();
    if (c.state !== "live" || camBusy()) return;
    const cmd = line();
    setLine("");
    hi = -1;
    if (cmd.trim()) setHistory((h) => [cmd, ...h.filter((x) => x !== cmd)].slice(0, 50));
    await c.link.write(new TextEncoder().encode(cmd + "\r"));
  }

  function key(e: KeyboardEvent) {
    const h = history();
    if (e.key === "ArrowUp" && h.length) {
      e.preventDefault();
      hi = Math.min(hi + 1, h.length - 1);
      setLine(h[hi]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      hi = Math.max(hi - 1, -1);
      setLine(hi < 0 ? "" : h[hi]);
    }
  }

  return (
    <div class="section">
      <Notice kind="warn">
        Commands go straight to the camera. <code>write</code> and <code>nand</code> commands change it, and reading
        unmapped addresses can power it off. <a href={doc("docs/access/serial-shell.md")}>Shell reference</a>.
      </Notice>
      <pre class="term" ref={pane} aria-live="off">
        {text()}
      </pre>
      <form class="term-input" onSubmit={send}>
        <input
          type="text"
          value={line()}
          onInput={(e) => setLine(e.currentTarget.value)}
          onKeyDown={key}
          placeholder={camBusy() ? `Waiting for ${camBusy()} to finish` : "help"}
          disabled={!!camBusy()}
          aria-label="Shell command"
          autocomplete="off"
          spellcheck={false}
        />
        <button class="btn" type="submit" disabled={!!camBusy()}>
          Send
        </button>
        <button class="btn" type="button" onClick={() => setText("")}>
          Clear
        </button>
      </form>
    </div>
  );
}
