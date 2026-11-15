# The DSMC2 Debug Shell

The DSMC2 firmware contains an interactive debug shell (`dsh>`). It gives live
memory read/write, NAND and filesystem access, the settings database, module
inventory, and low-level test routines. It is the tool for diagnosing a dead or
sick body and for pulling data the normal interfaces will not give you.

This page explains what gates it, how to turn it on, and how to reach it.

## Two gates

The shell has two independent gates. Both must be satisfied.

1. **Enable gate** — the CDB (settings database) flag `DEBUG_ENABLE_SHELL=1`.
   This flag is **not** in the RCP SDK. It is only settable through a `.preset`
   file (see below).
2. **Routing gate** — the CTRL port's serial protocol must be set to
   **Serial Shell = 4**. The firmware's protocol enum is
   `NONE=0, ET=1, TA=2, DEPRECATED=3, SERIAL_SHELL=4, RCP=5, RCP_3D=6,
   CAM_TO_CAM=7, COOKE_I=8, PRESTON=9`. The parameter is `SERPROTO` ("Ctrl:
   Protocol"); the second serial port uses `SERPROT2`.

The routing gate (`SERPROTO=4`) can be set two ways — over WiFi/RCP, or in the
preset. The enable gate (`DEBUG_ENABLE_SHELL=1`) can only be set by a preset.
So in practice you always need a preset, and it is simplest to put both settings
in the one preset.

> RCP alone does **not** give you the shell. RCP can set `SERPROTO=4` (routing is
> public), but it **cannot** set `DEBUG_ENABLE_SHELL` — it is a CDB flag below the
> RCP tier. This was tested: sending `S:DEBUG_ENABLE_SHELL` (and `G:...`) over RCP
> makes the camera log `error (1) creating system message from RCP message`, i.e.
> it rejects the parameter. So a preset is the only way to set the enable gate,
> over WiFi or otherwise.

## Route A — preset on a MINI-MAG (the reliable way)

1. Use the ready-made
   [`enable-shell.preset`](../../tools/preset/presets/enable-shell.preset), or build
   one that sets both flags — `DEBUG_ENABLE_SHELL=1` and
   `COMM_SERIAL_PORT_PROTOCOL=4` (that CDB name is the `SERPROTO` routing gate):
   `cargo run --manifest-path tools/dsmc-fw-core/Cargo.toml -- preset build shell.preset 7 DEBUG_ENABLE_SHELL=1 COMM_SERIAL_PORT_PROTOCOL=4`.
   See [tools/preset/](../../tools/preset) and [docs/keys.md](../firmware/keys.md).
2. Copy the preset into **`/force_preset/`** on a MINI-MAG. The camera applies it
   at boot. It auto-deletes each boot, so re-stage it every time.
3. Boot the camera with the serial bridge connected
   ([hardware/serial-bridge](../../hardware/serial-bridge)). The shell banner
   appears a few seconds into boot and the prompt is `(NN%/NN%) dsh>`.
4. The shell stays interactive. (An earlier belief that it was only open for a
   few seconds was wrong — it was a symptom of the 3.3V MAX3232 transmit problem
   described in the serial-bridge notes. With a clean 5V bridge the shell
   persists.)

Serial is 115200 8N1.

## Route B — WiFi/RCP for the routing half

If the camera already has `DEBUG_ENABLE_SHELL=1` applied (from a preset), you can
flip the routing gate over WiFi with RCP by setting `SERPROTO=4`, instead of
putting it in the preset. You still needed a preset for the enable gate, so this
is only a convenience. WiFi cannot reach the shell session itself — once routed,
the shell is raw serial on the CTRL port.

## What the shell can do

The top-level commands (RED-custom shell, 17 commands):

```
read   <addr> [end]   live memory read  (DDR @ 0x80000000 = running firmware/code)
write  <addr> <val>   live memory write
nand                  NAND / filesystem operations
cdb    sql "QUERY"    full SQLite access to the settings database
get / set             read / set a parameter
maint                 maintenance: module inventory, logs, board info
test                  low-level test routines (SATA, XAUI, ISP, loopback)
media / player / record_start
debug                 debug functions
ddr_test  wlan  app_start  shutdown  exit
```

`help <command>` lists its subcommands.

Useful specifics:

- `cdb sql "SELECT ..."` — the settings DB has ~2062 parameters. **Use
  double-quotes**; the shell splits on spaces.
- `maint module` — every attached module with its software and hardware version
  (for pairing and repair).
- `maint save_log` — writes the decrypted log to a SATA SSD (then decode it per
  [docs/log-decode.md](../firmware/log-decode.md), or it is already readable depending on the
  path). This command is not gated.
- `nand ls` / `nand info` / `nand show-partition-table` — filesystem browsing.
- `test sata active smartlogpage 0x90` — read a mag's SMART signature page (see
  the media folder).

## Two safety rules

1. **Only `read reg` known-valid memory.** DDR `0x80000000–0x81FFFFFF` is safe.
   Reading unmapped or peripheral addresses (for example `0xC0000000`, or OMAP
   crypto MMIO around `0x480Axxxx`) **faults the processor and powers the camera
   off**, losing the session. Do not probe arbitrary peripheral addresses.
2. **Never write a register before you have read it.** `write` is ungated and can
   leave a subsystem dead until reboot. Avoid blind writes to sensor/ISP/
   calibration registers in particular.

## One set of commands is compiled out (not a runtime lock)

Five NAND subcommands — `nand cat`, `nand cp`, `nand mv`, `nand base64`,
`nand show-sector` — are **not** gated by any flag. Their code was removed before
the firmware was compiled; each one is a stub that prints
`#define ENABLE_ADVANCED_SHELL_COMMANDS to enable this command` and returns.
There is no runtime switch to find, and setting `DEBUG_MFG_FEATURES_ENABLED` does
not unlock them (it was tried; it only suppressed some `maint` reporting).

The rest of the `nand` set — `show-partition-table`, `stats`, `info`, `df`, `du`,
`rm`, `gc`, `ln`, `xattr`, `mark-block`, `zlib`, `speed-test` — is real and works.
In particular `nand zlib` reads (decompresses) whole files, which is how the
on-NAND firmware image is pulled off the camera.

> Because the running firmware has no memory protection, the missing commands can
> in principle be supplied by writing code into free memory and pointing a stub at
> it. That is code injection, not unlocking, and is out of scope here.

## A quick trap to avoid

Do not boot with `DEBUG_MFG_FEATURES_ENABLED=1` for normal shell work. It gives
no benefit (it does not unlock the compiled-out commands) and it was observed to
break `maint module` / `maint print_boot_log` / `maint send_redinfo` for that
boot. Use a plain shell preset.

## WiFi: ad-hoc vs infrastructure

Two different machines hide behind `WIFI_MODE`. **Ad-hoc (1) brings itself up; infrastructure (2) does
not** — it needs a connect that the shell cannot issue. Verified on stock 7.4.1 (2026-11-15).

### Ad-hoc — works from the shell

```
cdb set WIFI_MODE 1                  # 0=off 1=ad-hoc 2=infrastructure
cdb set WIFI_ADHOC_ENCRYPTION 0      # open network
wlan status                          # Link: Up, SSID WEAPON_<serial>, IP 192.168.100.76
```

Join `WEAPON_<serial>` on the host, then use RFTP (`rftp-rs 192.168.100.76 list /`). This is the
bootstrap network: it exists even when the app is stalled in a startup error.

### Infrastructure — set the parameters here, then Connect in the UI (or reboot)

```
cdb set WIFI_MODE 2
cdb set WIFI_AP_SSID "<SSID>"
cdb set WIFI_AP_ENCRYPTION 3         # 3 = WPA2 (the radio has no WPA3)
cdb set WIFI_AP_PASSPHRASE "<passphrase>"
cdb set WIFI_DHCP_ENABLE 1
cdb get WIFI_MODE ; cdb get WIFI_AP_SSID ; wlan status
```

`wlan status` will read `Mode: Infrastructure, Link: Not connected` — that is expected, not a failure.
The radio only joins when the app raises its connect flag, and only two things do that:

- the UI's **Connect** button (`RCP_PARAM_WIFI_CONNECT` → `msg_set_wifi_ap_connect`), or
- **boot**: `initInterface` reads these same CDB values and raises the flag when mode is 2 and the
  SSID is non-empty.

Pressing **Connect** after the writes above associates immediately (bench-verified, no reboot). For a
body whose panel is unusable there is no Connect button, so write the five values and power-cycle: it
associates during boot with no UI and no messages.

Three traps worth knowing:

- **`WIFI_SSID_CACHE_1..4` are decorative.** Their `apply_order` is `-1`, so presets skip them and the
  app never reads them. The `SSID|passphrase` string a pulled `user.db` shows is UI bookkeeping only.
  The live parameters are `WIFI_AP_*` ("AP" = the remote access point).
- **Mode must be written before the credentials.** `msg_set_wifi_ap_connect` drops the connect when
  `WIFI_MODE != 2` at the moment it runs.
- **`cdb set` writes the database, it does not apply.** `cdb apply-preset`, `cdb export-preset` and
  `debug sendMsg WIFI_MSG_SET_MODE …` were all tried on the bench and none produced an association.

The serial `#` RCP handler is GET-only, so RCP SET of `WLANMODE` / `WLANAPID` / `WLANAPEN` /
`WLANAPPW` / `WIFI_CONNECT` is a TCP-9999 feature, not a serial one. Once any network is up, RCP SET
over 9999 is the only path that changes mode without a UI or a reboot.

Proof of life after association: `rftp-rs <ip> list /` returns the media and root listings (RFTP on
TCP 1111). TCP 9999 probing closed is normal — the RCP listener is only open while a client session
holds it.
