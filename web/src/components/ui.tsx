import { createSignal, JSX, Show } from "solid-js";
import { fmtBytes } from "../download";

/** The viewfinder-framed file drop target. */
export function DropFrame(props: {
  title: string;
  hint: string;
  accept?: string;
  compact?: boolean;
  file?: { name: string; size: number } | null;
  onFile: (f: File) => void;
}) {
  const [over, setOver] = createSignal(false);
  let input!: HTMLInputElement;
  const take = (f?: File | null) => f && props.onFile(f);
  return (
    <div
      class="frame"
      classList={{ over: over(), compact: props.compact }}
      role="button"
      tabindex="0"
      aria-label={props.title}
      onClick={() => input.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.click())}
      onDragOver={(e) => (e.preventDefault(), setOver(true))}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer?.files[0]);
      }}
    >
      <i class="c1" />
      <i class="c2" />
      <div class="inner">
        <strong>{props.title}</strong>
        <span>{props.hint}</span>
      </div>
      <Show when={props.file}>
        {(f) => (
          <span class="meta">
            {f().name} ({fmtBytes(f().size)})
          </span>
        )}
      </Show>
      <input
        ref={input}
        type="file"
        hidden
        accept={props.accept}
        onChange={(e) => {
          take(e.currentTarget.files?.[0]);
          e.currentTarget.value = "";
        }}
      />
    </div>
  );
}

export function Notice(props: { kind?: "error" | "info" | "warn"; children: JSX.Element }) {
  return (
    <div class="notice" classList={{ info: props.kind === "info", warn: props.kind === "warn" }} role={props.kind === "error" || !props.kind ? "alert" : undefined}>
      {props.children}
    </div>
  );
}

export function Busy(props: { children: JSX.Element }) {
  return (
    <div class="busy" role="status">
      {props.children}
    </div>
  );
}

export function PageHead(props: { title: string; children: JSX.Element }) {
  return (
    <header class="page-head">
      <h1>{props.title}</h1>
      <p>{props.children}</p>
    </header>
  );
}

/** Run an async action with busy + error state. */
export function useTask() {
  const [busy, setBusy] = createSignal<string | null>(null);
  const [error, setError] = createSignal<string | null>(null);
  async function run<T>(label: string, f: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    setBusy(label);
    try {
      return await f();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  return { busy, error, setError, run };
}
