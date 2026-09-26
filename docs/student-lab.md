# Student Lab mode

Student Lab is an optional preference under **Settings → Student Lab**. It defaults off. Only the explicit browser preference `hs.studentLab=on` enables it; a missing, off, or unrecognized value opens the professional workspace. The setting is remembered in this browser and also works in read-only static exports. If browser storage is unavailable, a change applies for the current visit and Settings reports that limitation.

The dashboard is the entry point. It shows the datasets available from the local application or included in the export. Student Lab does not switch collections or restrict the list to the bundled school datasets. There is no separate school-versus-machine workspace selector.

When Student Lab is off, teaching guides and package materials are absent from navigation. Direct links to `#/start` and `#/package` return to the dashboard. The legacy `#/home` link also resolves to the canonical dashboard at `#/`.

When Student Lab is on, teaching resources and guided trajectory explanations become available. Settings remains accessible in either mode. Changing the preference preserves the selected dataset and analysis link, including its model and harness selection. Use **Return to work** in Settings to resume that analysis.

Machine capabilities depend on the runtime, not the teaching preference. The local application can manage datasets and run experiments. A static export serves its embedded recordings, does not request machine settings or open a live event stream, and cannot import, capture, launch, or fork live runs. Links to unavailable machine operations show a **Read-only export** explanation with a route back to the dashboard. Settings in an export contains the viewing preference without machine paths or API-key controls.

## Teaching workflow

1. Open a loaded trajectory set and choose a model/harness family.
2. Open Trajectories. A selection-level note distinguishes passed, failed, and unknown hidden outcomes, and states how many matching rows are displayed.
3. Read each run's short observed-outcome takeaway. Expand the list to reach additional runs, including those after the first twenty.
4. Open a row for **What happened → Investigate → Why it matters → Evidence**.
5. Follow an evidence link to the recorded event, or expand the task description and final patch.

The explanations are deterministic, retrospective descriptions of the recording. They do not require an API key, call a model, predict future outcomes, or claim that an intervention would have saved tokens. Missing outcomes and incomplete usage remain unknown. Successful execution of a test tool is distinguished from a successful test result.

Student mode replaces the finished-run risk presentation with a guided recording view. Dedicated Sentinel, fork, and legacy canvas routes are outside this teaching view; the Sentinel analysis panel and A/B setup control are omitted. Switching the preference does not modify recorded data.

## Scope and distribution

This switch is a learning/presentation preference, **not authentication, authorization, or a confidentiality boundary**. The local application still contains its standard-mode code. A student distribution needs an independent source, asset, data, and archive audit before publication; hiding a control does not remove its implementation or data from a package.

No new research controller, predictive scoring model, stopping policy, or correction experiment is implemented by this feature.

## Verification

`AppMode.dom.test.jsx` covers the professional default, explicit opt-in, unchanged dataset availability, teaching navigation, canonical routes, and returning to the selected analysis. `AppMode.static.dom.test.jsx` covers the same preference with exported data and blocks links to live operations without network requests. `StudentLab.dom.test.jsx` covers guided explanations, evidence navigation, unknown results, truncation, pagination, errors, and teaching-view guards. Existing analysis and settings tests remain part of the full suite.
