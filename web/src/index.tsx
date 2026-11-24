import { render } from "solid-js/web";
import { A, HashRouter, Route, RouteSectionProps } from "@solidjs/router";
import { Show } from "solid-js";
import { conn } from "./state";
import Home from "./pages/Home";
import Logs from "./pages/Logs";
import Firmware from "./pages/Firmware";
import Presets from "./pages/Presets";
import CameraPage from "./pages/Camera";
import { REPO } from "./links";
import "./styles.css";

if (import.meta.env.DEV && new URLSearchParams(location.search).has("mock-camera")) await import("./dev/mock-serial");

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M3 10V3h7M22 3h7v7M29 22v7h-7M10 29H3v-7" fill="none" stroke="currentColor" stroke-width="2.5" />
      <circle cx="16" cy="16" r="3.5" fill="var(--tally)" />
    </svg>
  );
}

function Layout(props: RouteSectionProps) {
  return (
    <div class="app">
      <aside class="rail">
        <A href="/" class="brand" end>
          <Mark />
          <div>
            <b>DSMC Workbench</b>
            <span>for RED DSMC1 and DSMC2</span>
          </div>
        </A>
        <nav class="nav" aria-label="Tools">
          <A href="/logs">Logs</A>
          <A href="/firmware">Firmware</A>
          <A href="/presets">Presets</A>
          <A href="/camera">Camera</A>
        </nav>
        <div class="rail-foot">
          <div class="live">
            <span class="tally" classList={{ on: conn().state === "live" }} />
            <Show when={conn().state === "live"} fallback="No camera connected">
              Camera connected
            </Show>
          </div>
          <span>Runs in your browser. Files never leave this computer.</span>
          <a href={REPO}>Source and docs</a>
        </div>
      </aside>
      <main>{props.children}</main>
    </div>
  );
}

render(
  () => (
    <HashRouter root={Layout}>
      <Route path="/" component={Home} />
      <Route path="/logs" component={Logs} />
      <Route path="/firmware" component={Firmware} />
      <Route path="/presets" component={Presets} />
      <Route path="/camera" component={CameraPage} />
    </HashRouter>
  ),
  document.getElementById("root")!,
);
