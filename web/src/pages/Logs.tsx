import { createMemo, createSignal, For, Show } from "solid-js";
import { Busy, DropFrame, Notice, PageHead, useTask } from "../components/ui";
import { readFile, saveBytes } from "../download";
import { tools } from "../tools";
import type { Explain, LogReport } from "../types";

const sevClass = (s: string) => (s === "Fatal" || s === "Critical" || s === "Error" ? "fail" : s === "Warning" ? "warn" : "info");
const PAGE = 4000;

export default function Logs() {
  const t = useTask();
  const [file, setFile] = createSignal<File | null>(null);
  const [bytes, setBytes] = createSignal<Uint8Array | null>(null);
  const [report, setReport] = createSignal<LogReport | null>(null);
  const [sel, setSel] = createSignal(0);
  const [issuesOnly, setIssuesOnly] = createSignal(true);
  const [limit, setLimit] = createSignal(PAGE);
  const [allFaults, setAllFaults] = createSignal(false);

  async function open(f: File) {
    setFile(f);
    setReport(null);
    const data = await readFile(f);
    setBytes(data);
    const r = await t.run("Decoding and triaging the log. A large log can take several seconds.", () =>
      tools.logTriage(data),
    );
    if (r) {
      setReport(r);
      setAllFaults(false);
      const firstFail = r.triage.sessions.find((s) => !s.passed);
      setSel(firstFail ? firstFail.index : r.triage.sessions.length - 1);
      setLimit(PAGE);
    }
  }

  async function saveDecoded() {
    const d = bytes();
    if (!d) return;
    const out = await t.run("Decoding", () => tools.logDecode(d));
    if (out) saveBytes((file()?.name ?? "log").replace(/\.(data|enc)$/i, "") + ".decoded.txt", out, "text/plain");
  }

  const failed = createMemo(() => report()?.triage.sessions.filter((s) => !s.passed).length ?? 0);
  const lines = createMemo(() => {
    const r = report();
    if (!r) return [];
    const s = r.sessions[sel()];
    if (!s) return [];
    const all = s.lines.map((text, i) => ({ n: i + 1, text, sev: s.severity[i] }));
    return issuesOnly() ? all.filter((l) => l.sev === "E" || l.sev === "W") : all;
  });

  return (
    <div class="page">
      <PageHead title="Logs">
        DSMC2 and DSMC1 EPIC and SCARLET bodies keep their log in <code>/log/log.data</code>, scrambled with a fixed XOR
        pad. Drop it here, or an already-decoded text log. Each power-on is a separate boot session.
      </PageHead>

      <DropFrame
        title="Drop a camera log"
        hint="log.data, a .enc log, or a decoded .txt"
        file={file()}
        onFile={open}
      />

      <Show when={t.busy()}>
        <Busy>{t.busy()}</Busy>
      </Show>
      <Show when={t.error()}>
        <Notice>{t.error()}</Notice>
      </Show>

      <Show when={report()}>
        {(r) => (
          <>
            <div class="verdict" classList={{ fail: !r().triage.healthy }}>
              <div>
                <h2>
                  {r().triage.healthy
                    ? "Every boot reached ready to record"
                    : `${failed()} of ${r().triage.sessions.length} boots did not reach ready to record`}
                </h2>
                <p>
                  {r().triage.stats.total_sessions} boot sessions, {r().triage.stats.total_lines.toLocaleString()} lines,{" "}
                  {r().triage.stats.total_errors.toLocaleString()} error lines and{" "}
                  {r().triage.stats.total_warns.toLocaleString()} warnings.
                </p>
              </div>
            </div>

            <section class="section">
              <header>
                <h2>Faults found</h2>
                <p>Known error signatures from the firmware's error dictionary, worst first.</p>
              </header>
              <Show
                when={r().triage.stats.error_frequencies.length}
                fallback={<p class="muted">No known fault signatures in this log.</p>}
              >
                <div class="faults">
                  <For each={allFaults() ? r().triage.stats.error_frequencies : r().triage.stats.error_frequencies.slice(0, 5)}>
                    {(e) => (
                      <article class="fault">
                        <div class="spread">
                          <h3>{e.title}</h3>
                          <div class="row">
                            <span class={`pill ${sevClass(e.severity)}`}>{e.severity}</span>
                            <span class="muted small">
                              {e.total_count}× in {e.sessions_affected.length} of {r().triage.stats.total_sessions} sessions
                            </span>
                          </div>
                        </div>
                        <dl>
                          <dt>Subsystem</dt>
                          <dd>{e.subsystem}</dd>
                          <dt>Cause</dt>
                          <dd>{e.meaning}</dd>
                          <dt>What to do</dt>
                          <dd>{e.action}</dd>
                          <dt>Example</dt>
                          <dd>
                            <code>{e.sample_line}</code>
                          </dd>
                        </dl>
                      </article>
                    )}
                  </For>
                </div>
                <Show when={!allFaults() && r().triage.stats.error_frequencies.length > 5}>
                  <div class="row">
                    <button class="btn small" onClick={() => setAllFaults(true)}>
                      Show {r().triage.stats.error_frequencies.length - 5} more faults
                    </button>
                  </div>
                </Show>
              </Show>
            </section>

            <section class="section">
              <header>
                <h2>Boot sessions</h2>
                <p>Select a session to read its lines below.</p>
              </header>
              <div class="table-wrap scroll-y">
                <table>
                  <thead>
                    <tr>
                      <th class="num">Session</th>
                      <th>Result</th>
                      <th>Duration</th>
                      <th>Furthest state</th>
                      <th>Failures</th>
                      <th class="num">Lines</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={r().triage.sessions}>
                      {(s) => (
                        <tr
                          onClick={() => (setSel(s.index), setLimit(PAGE))}
                          style={{ cursor: "pointer", "font-weight": sel() === s.index ? 600 : undefined }}
                          aria-selected={sel() === s.index}
                        >
                          <td class="num">{s.index}</td>
                          <td>
                            <span class={`pill ${s.passed ? "ok" : "fail"}`}>{s.passed ? "Ready" : "Failed"}</span>
                          </td>
                          <td class="nowrap">{s.meta.duration}</td>
                          <td>{s.furthest}</td>
                          <td class="small">{s.meta.failure_order.join(", ") || "None"}</td>
                          <td class="num">{s.meta.lines.toLocaleString()}</td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
            </section>

            <section class="section">
              <div class="spread">
                <h2>Session {sel()}</h2>
                <div class="row">
                  <label class="check">
                    <input type="checkbox" checked={issuesOnly()} onChange={(e) => setIssuesOnly(e.currentTarget.checked)} />
                    Errors and warnings only
                  </label>
                  <button class="btn small" onClick={saveDecoded}>
                    Download decoded log
                  </button>
                </div>
              </div>
              <Show
                when={lines().length}
                fallback={<p class="muted">No errors or warnings in this session.</p>}
              >
                <div class="log">
                  <For each={lines().slice(0, limit())}>
                    {(l) => (
                      <div class={l.sev === "E" ? "E" : l.sev === "W" ? "W" : undefined}>
                        <span class="n">{l.n}</span>
                        {l.text}
                      </div>
                    )}
                  </For>
                </div>
                <Show when={lines().length > limit()}>
                  <button class="btn small" onClick={() => setLimit(limit() + PAGE)}>
                    Show {Math.min(PAGE, lines().length - limit()).toLocaleString()} more lines
                  </button>
                </Show>
              </Show>
            </section>
          </>
        )}
      </Show>

      <ExplainBox />
    </div>
  );
}

function ExplainBox() {
  const [q, setQ] = createSignal("");
  const [res, setRes] = createSignal<Explain[] | null>(null);
  const t = useTask();
  const go = async (e: Event) => {
    e.preventDefault();
    if (!q().trim()) return;
    const r = await t.run("Looking up", () => tools.explain(q().trim()));
    if (r) setRes(r);
  };
  return (
    <section class="section">
      <header>
        <h2>Look up an error</h2>
        <p>Search by error code (0x1d), state number (0 to 35), or part of the camera (sdi, fan, sata, sensor, x12).</p>
      </header>
      <form class="row" onSubmit={go}>
        <input type="search" value={q()} onInput={(e) => setQ(e.currentTarget.value)} placeholder="0x1d" aria-label="Error code or keyword" />
        <button class="btn" type="submit">
          Look up
        </button>
      </form>
      <Show when={t.error()}>
        <Notice>{t.error()}</Notice>
      </Show>
      <Show when={res()}>
        {(r) => (
          <Show when={r().length} fallback={<p class="muted">Nothing matches “{q()}”.</p>}>
            <div class="faults">
              <For each={r()}>
                {(m) => (
                  <article class="fault">
                    <div class="spread">
                      <h3>{m.title}</h3>
                      <span class={`pill ${sevClass(m.severity)}`}>{m.severity}</span>
                    </div>
                    <dl>
                      <dt>Subsystem</dt>
                      <dd>{m.subsystem}</dd>
                      <dt>Cause</dt>
                      <dd>{m.meaning}</dd>
                      <dt>What to do</dt>
                      <dd>{m.action}</dd>
                    </dl>
                  </article>
                )}
              </For>
            </div>
          </Show>
        )}
      </Show>
    </section>
  );
}
