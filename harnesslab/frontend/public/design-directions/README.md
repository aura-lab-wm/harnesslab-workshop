# HarnessLab: five interface directions

Open `http://127.0.0.1:5173/design-directions/compare.html` with the existing Vite
development server running (`npm run dev` from `harnesslab/frontend`). Open
`index.html#studio`, `#desk`, `#notebook`, `#console`, or `#atlas` for an interactive
direction. The prototypes also render directly from disk; production links need
the running application. No production React components were changed.

## Human and task

A researcher or engineer has run the same coding tasks under several harnesses.
They need to understand what changed, distinguish a score from a reliable finding,
and inspect the runs behind the result. Students also need the interface to explain
the experimental structure without requiring a tour of the implementation.

The domain is controlled variables, apparatus, trials, paired comparisons,
trajectories, uncertainty, provenance, and scientific records. Every direction
keeps its claims tied to a named study and oracle.

## The five systems

| Direction | Natural color world | Signature | Structural choice |
| --- | --- | --- | --- |
| Notebook | Paper, graphite ink, cyanotype, binding gray, oxide annotations | Finding → figure → method, with a real annotation margin | A research note and table of contents instead of dashboard cards |
| Console | Graphite instrument body, inset charcoal, etched white, blue readouts, amber attention | Select a matrix cell and inspect its uncertainty and cost | Horizontal instruments above a full comparison matrix |
| Atlas | Drafting paper, blueprint blue, pencil gray, ink, neutral white | Study → model → harness branches with a linked evidence inspector | Spatial relationships instead of a flat navigation hierarchy |
| Desk | Archive paper, document white, charcoal, file-label gray, oxide annotation | Selection links the analysis to an adjacent evidence record | Persistent collection / analysis / inspector panes |
| Studio | Chalk, carbon, concrete gray, oxide ink, warm inset paper | Large paired effect → model slope chart → task-level decomposition | A measured finding leads; analysis follows |

The obvious defaults were a sidebar with a KPI-card grid, generic blue/purple
decoration, and a dense table as the first explanation. Each direction replaces
those with an experimental reading order and a palette from a physical research
setting. The Desk deliberately uses familiar navigation, but its working unit is
an evidence record, not a metric card.

## Shared specification

- A 4px spacing base. Primary control radius 4px; panels 4–6px. Studio uses square
  primary actions to match its typographic construction.
- Borders provide depth. No decorative gradients or drop shadows.
- Four text roles: primary, supporting, metadata, and muted annotation. Mono
  figures align values; units stay smaller and adjacent.
- Self-hosted Plex Sans / Plex Mono. Notebook adds Georgia for the research-note
  voice; Desk and Studio use a restrained system grotesk for their distinct density
  and scale. No external font requests.
- Active model and harness selections have explicit `aria-pressed` states. Controls
  have hover and visible keyboard focus, and rerendering preserves control focus.
- Exact cell intervals remain visible in the plot or linked inspector. A measured
  zero has zero bar length. Paired study effects are labeled separately from
  selected-model cell scores.
- Below 1000px, inspectors move below the work. Below 640px, navigation wraps,
  columns stack, and large data surfaces scroll internally. Atlas preserves the
  spatial graph rather than squeezing its nodes into unreadable blocks.

## Interactive coverage

- All five directions can be switched through the review bar, with deep links.
- Notebook and Desk model/harness choices update the interval chart and notes.
- Console cell selection updates the adjacent inspector.
- Atlas selection updates the highlighted path, cell values, and inspector.
- Studio model selection updates the highlighted slope series.
- Desk search filters the three study links and supports an empty result state.
- Links into studies, trajectories, experiments, reports and the guide open the
  existing app. These previews do not launch runs or write backend state.

These are populated direction prototypes, not a replacement app. Production work
after selection should carry the chosen system into study creation, run replay,
comparison, imports, settings, loading, empty, error, and disabled states. Those
production states are not represented by the fixed populated sample here.

## Data provenance

The figures are a fixed sample from `/api/results/live/metrics`, inspected on
2026-09-21. The full study has 576 runs, three models, eight harness conditions,
eight tasks, and three repeats. Six core harnesses account for 432 runs; two
sentinel variants account for the remaining 144. The previews explicitly label
the six-core-harness views.

Short context versus baseline is −23.6111 percentage points with task-paired
95% interval [−40.2778, −8.3333], aggregated across all three model families.
Individual cell confidence intervals are task-bootstrap intervals from the same
API. They are not binomial intervals. Point estimates of 100% with degenerate
bootstrap intervals describe this observed sample, not universal certainty.

## Recommendation

Use Studio's visual identity and introductory finding with Desk's persistent
navigation and selection/inspector behavior. This combines a memorable opening
with a coherent workspace for prolonged analysis. This is a proposal; no selected
system has been written into `.interface-design/system.md`.
