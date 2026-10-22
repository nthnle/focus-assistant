# Working on focus-assistant

- Commit and push straight to `main`; no side branches or pull requests
  unless asked.
- Run the tests before pushing: `.venv/bin/pytest` (they mock the model and
  all hardware, so they need no key, network or Pi).
- Every state change goes through `focus/actions.py`; never hold the state
  lock during a network or model call (see docs/architecture.md).
- When behaviour changes, update the docs and, if the picture changes, the
  diagrams in `docs/diagrams/` (edit the HTML, then re-export with
  `python docs/diagrams/render.py`; see docs/diagrams/README.md).
- Phone use comes from the desk camera (`focus-camera`); there are no
  charger reports any more. The status page (`focus-page`) is the e-ink
  panel's layout in a browser.
- Camera frames, and the model trained on them, are pictures of the user:
  never commit them (`frames/`, `*.onnx` are git-ignored).
