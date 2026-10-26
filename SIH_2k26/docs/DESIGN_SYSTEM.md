# Design system

Character: trust, clarity, order, accountability. Warm paper, ink navy, quiet borders. No purple/blue gradients, orbs, glassmorphism, grain, or oversized statistic tiles.

## Tokens (`frontend/src/styles/tokens.css`)

| Token | Value | Use |
|---|---|---|
| `--paper` | `#F7F6F2` | page background |
| `--paper-2` | `#EFEDE6` | recessed surfaces |
| `--surface` | `#FCFBF8` | raised surfaces (forms, panels) |
| `--ink` | `#18334A` | primary structure, primary buttons |
| `--text` | `#23282D` | body text (deep charcoal) |
| `--text-muted` | `#586873` | secondary text (≥ 4.5:1 on paper) |
| `--slate` | `#647F91` | decorative lines, secondary marks |
| `--teal` | `#3B6C68` | active state, focus (darkened from `#477C78` for AA text contrast) |
| `--amber` | `#9A6A1F` text / `#C18A43` fills | pending, deadlines |
| `--green` | `#2F6B47` | success |
| `--red` | `#A33A32` | error |
| `--line` | `#D9D6CC` | hairline borders |

Semantic state is never colour-only: every status carries a text label and a shape/icon.

## Type

- Interface: **Inter Variable**.
- Editorial headings: **Fraunces Variable** (used for display headings only).
- Technical metadata (IDs, timestamps): **IBM Plex Mono**.
- Fonts are bundled locally via `@fontsource*`; system fallbacks are declared.

## Space and shape

4-px base scale (`--s-1` … `--s-9`). Radii are small (2–6 px). Panels use 1-px borders instead of shadows. Content max width 72 rem.

## Components (`frontend/src/components/ui`)

`Button` (link/button variants), `Field` (label, hint, error wiring), `PasswordField`, `Alert`, `EmptyState`, `ErrorState`, `LoadingState`, `AccessDenied`, `Badge`, `Wordmark`. Dashboard primitives live in `features/dashboard`.

## Interaction

- Visible focus ring (2-px teal, offset).
- Motion limited to the workflow instrument marker and 150 ms state transitions. Disabled entirely under `prefers-reduced-motion`; the instrument then shows a static, complete route.
- The instrument has a pause/play control and keyboard-operable stages.
- Illustrative content is labelled "Illustrative".
