# Compatible SSDs — What to Buy (2026)

The camera gates full rate on three things: the drive's **model string**
(whitelist), an **MD5 signature** it can write itself, and a **~29-field ATA
IDENTIFY match** (`AuthenticateREDMedia`). The first two are easy to satisfy on a
buyable drive. **The third is not** — no stock retail SSD has been shown to pass
it, because the check compares capability/version words baked into the drive's
controller firmware against the exact values of the drive RED shipped.

So the practical rule for 2026 is:

> **Buy any decent mSATA/M.2-SATA SSD, then apply the `media-any` camera firmware
> patch.** The patch is the only demonstrated way to run a non-genuine drive at
> full rate. Without it, even an approved-model, correctly-signed, correct-capacity
> drive throttles to ~30 MB/s on the IDENTIFY check — confirmed on a real
> `SAMSUNG MZMTE1T0HMJH-00000` (a model RED itself whitelisted).

See [authentication.md](authentication.md) for the three checks and the patch.

## Rules

- **SATA only.** Use mSATA or M.2-SATA. Do **not** use NVMe (M.2 PCIe). Do not
  use CFexpress/CF/SD.
- Any drive near **500 MB/s** write comfortably meets the ~300 MB/s R3D data rate.
  Check sustained write, not burst.
- The drive **must answer the vendor SMART log page 0x90**. If its controller
  rejects that log, the camera's read fails and the drive **does not mount at
  all**. This is the one hard controller requirement.
- Beyond that, with the `media-any` patch the controller, NAND, capacity, and even
  the model string stop mattering for the mount and for full rate. Without the
  patch, the controller's reported IDENTIFY fields decide full rate, and a retail
  drive fails them.

## Buyable mSATA SSDs (any of these + the patch)

Pick on speed, capacity, and endurance — not on model string, since the patch
removes the whitelist.

| Drive | Sizes | Notes |
|---|---|---|
| Kingston KC600 mSATA | 256 GB / 512 GB / 1 TB | Still manufactured. SATA III, ~550 MB/s. Silicon Motion controller. |
| KingSpec MT mSATA | up to 1 TB | In stock, low cost, budget NAND. |
| Fanxiang / SHARKSPEED / Kingchuxing mSATA | 512 GB / 1 TB | eBay / Amazon, ~30–130 USD, budget NAND. |
| Samsung PM851 mSATA 1 TB (used) | 1 TB | Reports an approved string and matching capacity, but still fails the IDENTIFY check — needs the patch like any other drive. |

mSATA is legacy but still made and in stock. M.2-SATA is the newer option and
allows capacities past 960 GB (KipperTie's approved mag uses M.2-SATA at 2 TB).

## Why "native pass, no patch" does not work on a buyable drive

Several stock OEM SSDs report a model string that is already in the media table
(Samsung PM851 → `RED 1TB V1`; Micron M500/M500IT; Kingston SMS100). Earlier
revisions of this page called these an "easy path" that needed no flashing and no
patch. **That was wrong.** Reporting an approved string and the matching capacity
clears the whitelist and the sector-count field — but `AuthenticateREDMedia` also
compares ~29 other ATA IDENTIFY fields, and the capability/version words
(IDENTIFY words 69, 75, 76, 80, 81, 83, 84, 87, 117, 119, 120 and others) differ
between a retail controller's firmware and the drive RED shipped. Any mismatch
forces ~30 MB/s. This was seen directly on a PM851: model matched, signature
valid, capacity exact, and the camera still logged *"NOT a RED product"* and
capped it to 30 MB/s on those capability words.

You cannot change those words on the drive without reflashing its controller, and
no public tool does that cleanly for these controllers. So the model string being
"native" saves you a model-string flash, but it does **not** get you full rate.
The patch does.

## Paths to full rate, ranked

### 1. Any drive + `media-any` camera firmware patch (demonstrated)

Buy any mSATA/M.2-SATA SSD that answers SMART page 0x90. Apply the `media-any`
patch (RAM test first, then NAND). It forces the signed flag and skips
`AuthenticateREDMedia`, so **any** drive runs at the media row's full rate, model
string and IDENTIFY words irrelevant. This is the only path confirmed to give full
rate on a non-genuine drive. The drive needs no flashing; you do not even need to
match the model string.

### 2. Genuine SM2246EN mSATA + edited `.rmf` (unproven, no patch)

The MINI-MAG controller firmware (`.rmf`) is plaintext and carries the full ATA
IDENTIFY template — including the capability words gate 3 checks. If the camera's
Upgrade > Media flasher will write an edited `.rmf` to a non-RED SM2246EN drive
with compatible NAND, that drive could pass all three gates **natively, no camera
patch**. This is a research lead, not a method: untested, and a NAND mismatch can
brick the drive. See [mag-firmware.md](mag-firmware.md).

### 3. SATA bridge that overrides IDENTIFY (unproven for full rate)

Some SATA bridge chips override the model string and WWN a drive reports. That can
satisfy the whitelist and the signature (the camera signs what it sees). But full
rate needs the bridge to also present the right values for ~29 IDENTIFY fields
including SATA caps and queue depth — no bridge is known to override that whole
set. Treat this as a mount/whitelist trick, not a full-rate solution, unless a
specific bridge is verified to pass gate 3.

## Field confirmation

This matches what the community and approved vendors have reported, with one
correction:

- Reduser analysis of JinniTech's method said the camera reads only the **SSD
  model number** and the **SMART log**. That is true for the *mount* decision, but
  it is incomplete for full rate — the camera also compares the ATA IDENTIFY
  fields, which is why purpose-built mags use specific controller firmware, not
  stock SSDs.
- Approved third-party mags are **purpose-built drives**, not stock SSDs. They
  pass gate 3 because their controller firmware reports the expected IDENTIFY
  fields. A stock SSD does not, which is the whole reason the camera patch exists.
- RED's statement that the custom part "is not on the drives, but in the camera"
  is about the *signature*; the IDENTIFY-field check shows the camera also cares
  what the drive's controller reports.

## Controller requirements (corrected)

Two things the controller must do, and one myth:

1. **Answer vendor SMART log page 0x90.** Hard requirement — no answer, no mount.
2. **For full rate without the patch:** report the exact ~29 IDENTIFY fields of
   the spec row. No stock retail controller does. This is why the patch is the
   practical route.

The myth, stated in earlier revisions: *"there is no controller, link-speed, or
queue check in the accept path."* False. `AuthenticateREDMedia` checks SATA
capabilities (IDENTIFY word 76), queue depth (word 75), command-set and version
words, and more. The accept path very much depends on controller-reported values
— which is exactly why a buyable drive needs the camera patch.

## Drives RED validated themselves

A later DSMC2-family dev build (DXL2) carried five extra media-table entries labelled
**"Dev. Only"**. They are ordinary consumer mSATA SSDs that RED enabled for its own
testing — proof that the media gate is **policy, not a hardware limit**:

| dev label | actual drive |
|---|---|
| 840 EVO – Dev. Only | Samsung 840 EVO 1 TB mSATA |
| TS512 – Dev. Only | Transcend TS512GMSA370 |
| RED 240GB V2 – Dev. Only | Transcend TS240M7MRE80IM2 |
| RED 512GB V2 – Dev. Only | Transcend TS480M7MRE80IM2 |
| Micron M550 512GB – Dev. Only | Micron M550 (MTFDDAT512MAY) |

These entries are **not** in the shipping 7.4.1 firmware, so a stock camera will not
authenticate any of them natively — you still need the `media-any` patch (or a drive
that already matches a shipping table row). But it shows that a plain Samsung, Transcend,
or Micron mSATA is a drive RED itself trusted; the lock is a business decision.
