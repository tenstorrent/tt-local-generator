# New Model Support — Generation Log

Records proving each model generates successfully on QB2 (P300X2, 4 × Blackhole chips).
Kept in the **"New Model Support"** in-app playlist (id: `e46b4782-b991-48bb-b5dd-ada1b0da1b2b`).

---

## Wan2.2-T2V-A14B-Diffusers

| Field | Value |
|---|---|
| Record ID | `bd6bcb0e-f480-418b-b2df-662d92577d1c` |
| Date | 2026-07-01 |
| Hardware | QB2 — P300X2 (2 × P300, 4 Blackhole chips, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | ~6 min (TTNN kernel cache warm; first-run ~55 min) |
| Generation time | **459 s (~7.7 min)** |
| Inference steps | 30 |
| Output | MP4 video, 480×848, 168 frames |
| Prompt | *bird's-eye — straight down: two men shooting at each other at dawn, a yellow butterfly watching, a yellow butterfly crosses a street, both ways without looking, house with someone who moved, echoes and low-level wrongness* |
| Video path | `/home/ttuser/.local/share/tt-video-gen/videos/20260701_151946_bd6bcb0e.mp4` |

**Notes:** First successful generation on v0.17.0 image after fixing `_wan22_pipeline_args` patch
(`"prompt"` → `"prompts": [...]` to match the updated `WanPipeline.__call__` signature).

---

## FLUX.1-schnell

*Generation in progress — entry will be filled in once the run completes.*

| Field | Value |
|---|---|
| Record ID | TBD |
| Date | 2026-07-01 |
| Hardware | QB2 — P300X2 |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | TBD |
| Generation time | TBD |
| Inference steps | TBD |

---

## Motif-Image-6B-Preview

*Generation in progress — entry will be filled in once the run completes.*

| Field | Value |
|---|---|
| Record ID | TBD |
| Date | 2026-07-01 |
| Hardware | QB2 — P300X2 |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | TBD |
| Generation time | TBD |
| Inference steps | TBD |

---

## FLUX.1-schnell (Tenstorrent)

| Field | Value |
|---|---|
| Record ID | `336e4296-354a-4943-8dba-9393e7718cd7` |
| Date | 2026-07-02 |
| Hardware | QB2 — P300X2 (4× Wormhole p300c, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | **2311s** (~38 min) |
| Generation time | **17s** |
| Output | JPEG image |
| Prompt | *a talking beet sitting upright in a folding chair, a Tang dynasty scholar's garden: bamboo, a rock, a pavilion, a man who has nowhere to be, the pale blue of a phone screen in total darkness, Powell & Pressburger — color as emotion, dance, Technicolor myth, wide aperture, masterpiece* |
| File path | `unknown` |

---

## FLUX.1-schnell (Tenstorrent)

| Field | Value |
|---|---|
| Record ID | `336e4296-354a-4943-8dba-9393e7718cd7` |
| Date | 2026-07-02 |
| Hardware | QB2 — P300X2 (4× Wormhole p300c, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | **150s** (~2 min) |
| Generation time | **21s** |
| Output | JPEG image |
| Prompt | *a talking beet sitting upright in a folding chair, a Tang dynasty scholar's garden: bamboo, a rock, a pavilion, a man who has nowhere to be, the pale blue of a phone screen in total darkness, Powell & Pressburger — color as emotion, dance, Technicolor myth, wide aperture, masterpiece* |
| File path | `unknown` |

---

## Motif-Image-6B-Preview (Motif Technologies)

| Field | Value |
|---|---|
| Record ID | `0d33adcf-bd04-4fca-bca9-13ef25242bc0` |
| Date | 2026-07-02 |
| Hardware | QB2 — P300X2 (4× Wormhole p300c, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.9.0-c180ef7` |
| Warmup time | **900s** (~15 min) |
| Generation time | **51s** |
| Output | JPEG image |
| Prompt | *a wrestler nobody has thrown in seven years lifting his hands before the match, a Hellenistic library at Alexandria — scrolls floor to ceiling, one scholar looking for one line, cool blue of pre-dawn, oil painting, 8K, bokeh* |
| File path | `unknown` |

---

## Motif-Image-6B-Preview (Motif Technologies)

| Field | Value |
|---|---|
| Record ID | `0d33adcf-bd04-4fca-bca9-13ef25242bc0` |
| Date | 2026-07-02 |
| Hardware | QB2 — P300X2 (4× Wormhole p300c, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.9.0-c180ef7` |
| Warmup time | **960s** (~16 min) |
| Generation time | **41s** |
| Output | JPEG image |
| Prompt | *a wrestler nobody has thrown in seven years lifting his hands before the match, a Hellenistic library at Alexandria — scrolls floor to ceiling, one scholar looking for one line, cool blue of pre-dawn, oil painting, 8K, bokeh* |
| File path | `unknown` |

---

## FLUX.1-schnell (Tenstorrent)

| Field | Value |
|---|---|
| Record ID | `0d33adcf-bd04-4fca-bca9-13ef25242bc0` |
| Date | 2026-07-02 |
| Hardware | QB2 — P300X2 (4× Wormhole p300c, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | **150s** (~2 min) |
| Generation time | **58s** |
| Output | JPEG image |
| Prompt | *a wrestler nobody has thrown in seven years lifting his hands before the match, a Hellenistic library at Alexandria — scrolls floor to ceiling, one scholar looking for one line, cool blue of pre-dawn, oil painting, 8K, bokeh* |
| File path | `unknown` |

---

## Z-Image-Turbo (Tongyi-MAI)

| Field | Value |
|---|---|
| Record ID | `6eb7dae8-5467-43d7-badb-1d0815a1aa3f` |
| Date | 2026-07-02 |
| Hardware | QB2 — P150X4 (4× Blackhole p150, mesh (1,4)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | **7891s** (~131 min) |
| Generation time | **13s** |
| Output | JPEG image |
| Prompt | *Mrs. Dalloway buying flowers herself, because Scrope Purvis thought she looked so young, a 7-Eleven at 1am in a suburb you grew up in, strobe at 5fps, Kelly Reichardt — quiet Pacific Northwest, working-class women, long empty pause, award-winning, wide aperture* |
| File path | `unknown` |

---

## Motif-Image-6B-Preview (Motif Technologies)

| Field | Value |
|---|---|
| Record ID | `6eb7dae8-5467-43d7-badb-1d0815a1aa3f` |
| Date | 2026-07-02 |
| Hardware | QB2 — P300X2 (4× Wormhole p300c, mesh (2,2)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.9.0-c180ef7` |
| Warmup time | **7952s** (~132 min) |
| Generation time | **16s** |
| Output | JPEG image |
| Prompt | *Mrs. Dalloway buying flowers herself, because Scrope Purvis thought she looked so young, a 7-Eleven at 1am in a suburb you grew up in, strobe at 5fps, Kelly Reichardt — quiet Pacific Northwest, working-class women, long empty pause, award-winning, wide aperture* |
| File path | `unknown` |

---

## Z-Image-Turbo (Tongyi-MAI)

| Field | Value |
|---|---|
| Record ID | `6eb7dae8-5467-43d7-badb-1d0815a1aa3f` |
| Date | 2026-07-02 |
| Hardware | QB2 — P150X4 (4× Blackhole p150, mesh (1,4)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | **7861s** (~131 min) |
| Generation time | **13s** |
| Output | JPEG image |
| Prompt | *Mrs. Dalloway buying flowers herself, because Scrope Purvis thought she looked so young, a 7-Eleven at 1am in a suburb you grew up in, strobe at 5fps, Kelly Reichardt — quiet Pacific Northwest, working-class women, long empty pause, award-winning, wide aperture* |
| File path | `unknown` |

---

## Z-Image-Turbo (Tongyi-MAI)

| Field | Value |
|---|---|
| Record ID | `b04d3cac-afcb-43f9-9c73-47d6d4830354` |
| Date | 2026-07-02 |
| Hardware | QB2 — P150X4 (4× Blackhole p150, mesh (1,4)) |
| Docker image | `ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10` |
| Warmup time | **150s** (~2 min) |
| Generation time | **48s** |
| Output | JPEG image |
| Prompt | *a woman buying canned goods in a supermarket that smells faintly wrong, an Aboriginal sacred site in the Kimberley: a rock painting 40,000 years old, no railing, sodium yellow of a freeway overpass at night, Larisa Shepitko — Soviet spiritual intensity, white light, female heroism in snow, long exposure, professional photography* |
| File path | `unknown` |
