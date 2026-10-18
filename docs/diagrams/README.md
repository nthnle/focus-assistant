# Diagrams

| File | Shows |
|---|---|
| `architecture-simple.html` | The parts at a glance: phone, charger, the Pi's services, speakerphone, model host |
| `workflow-simple.html` | A weekday at a glance, with a pause, one voice turn and the offline fallback |
| `week-simple.html` | A week at a glance: goals, a day off, Friday's wrap-up and where the routine tip comes from |
| `cheatsheet.html` | The desk card: what to do every workday, every week, and now and then (shown in docs/routine.md) |
| `feedback-simple.html` | Improving the coach: the reply log, automatic checks, ratings and `focus feedback` |
| `architecture.html` | The same parts in detail: what each arrow carries, keys, trust boundaries |
| `workflow.html` | The weekday in detail, with timings |

Each HTML file is the source. Open it in a browser to see it (light or dark
follows the browser). Every box and arrow is placed by hand in pixels, so
when you move a box, move the arrows that touch it too.

After an edit, re-export the PNGs that the docs show:

```bash
pip install playwright && playwright install chromium
python docs/diagrams/render.py                  # all of them
python docs/diagrams/render.py workflow-simple  # one
```

Keep them in step with the code: a new service, a renamed setting or a
changed timer means a matching change here.
