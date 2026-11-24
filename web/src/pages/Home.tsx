import { A } from "@solidjs/router";
import { serialSupported } from "../serial";

export default function Home() {
  return (
    <div class="page">
      <header class="page-head">
        <h1>Look inside your RED camera</h1>
        <p>
          Read what your camera logged, open RED firmware packages, build presets, and run a health check on a live
          body. Made for DSMC2 bodies; most of it also works on DSMC1 EPIC and SCARLET, and the firmware tool opens RED
          ONE updates. Everything runs in this browser tab: your files and your camera's data are never uploaded.
        </p>
      </header>

      <div class="tools">
        <A href="/logs">
          <h3>Logs</h3>
          <p>
            Drop the camera's <code>log.data</code>. See whether each boot reached ready-to-record, which faults it hit,
            and what to check for each one.
          </p>
        </A>
        <A href="/firmware">
          <h3>Firmware</h3>
          <p>
            Open a RED firmware download, as the zip or the file inside it. List the models a combo build covers and
            extract every file it installs. Also unpacks RED ONE <code>redone.su</code> updates.
          </p>
        </A>
        <A href="/presets">
          <h3>Presets</h3>
          <p>
            Read and edit <code>.preset</code> files, or build a new one, such as the preset that turns on the debug
            shell.
          </p>
        </A>
        <A href="/camera">
          <h3>Camera</h3>
          <p>
            Connect over the CTRL port and run the same self-test as <code>dsmcdbg</code>: rails, temperatures, sensor
            lanes, power sequencers and I²C devices, each checked against a healthy reference body. For DSMC2 and DSMC1
            EPIC and SCARLET bodies.
            {serialSupported() ? "" : " Needs Chrome or Edge on a desktop."}
          </p>
        </A>
      </div>
    </div>
  );
}
