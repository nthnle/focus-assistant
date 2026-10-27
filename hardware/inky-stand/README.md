# Inky stand

A printable desk stand for the Inky Impression 4.0" (Spectra 6) e-ink panel:
a frame with an even border, leaning 20° back on a slim two-armed foot.

<img alt="The stand with the panel in it, from the front" src="inky-stand.png" width="600">

- **Only the screen shows.** The window is the size of the active area. The
  frame sits 2 mm in front of the board, so it covers the glass's edge and
  the display's own ribbon without touching them.
- **Only the four corners touch the board.** The panel screws on from
  behind, through its M2 corner holes into posts under the frame. The back
  stays open, so the Hack3r (or a ribbon plug) and the buttons on the back
  are free.
- **The foot is two arms, open between them**, so the ribbon to the Pi drops
  straight down.

## Print

`inky-stand.stl` is ready to print as it is. It lies on the frame's front
face, and the two arms rise from it, leaning 20° past vertical. No supports
are needed.

- PLA or PETG, 0.2 mm layers, 3 walls, 20% infill. Matte black matches the
  panel's black border.
- About 118 × 90 mm on the bed and 73 mm tall.
- The arms are tall and thin while printing. If your printer shakes, a brim
  helps them stay put.

## Put it together

1. Lay the panel face down in the frame. Its corner holes sit over the four
   posts.
2. Four **M2 × 6 mm** screws, from behind. They cut their own thread, so stop
   when they're snug. A longer screw would come out of the front.
3. Plug the ribbon back in and let it drop between the arms.

**Pressing the buttons:** the stand is light, so a one-finger push on a
button from behind can tip it forward. Pinch instead, with your thumb on the
front and your finger on the button, or stick the arms down with museum gel.

## Change it

`inky-stand.scad` is the source. Open it in [OpenSCAD](https://openscad.org),
change the settings at the top (the Customizer lists them), then export a new
STL with **Render** (F6) and **Export as STL**. `layout = "desk"` shows the stand
upright, with the board in place to check the fit.

| Setting | Default | |
|---|---|---|
| `tilt` | `20` | Degrees back from upright |
| `buttons_side` | `left` | As you face the screen. Set `right` if your buttons and the screen's wider margin are on the other side |
| `glass_clear` | `2` | Gap between the board's face and the frame. Raise it if the frame touches the glass |
| `window_margin` | `1` | How much bigger than the screen the window is, each side |
| `pilot_d` | `1.7` | Screw holes. Use `1.6` if the screws are loose, `1.8` if they're hard to turn |
| `foot_depth` | `70` | How far the arms reach back |

The board's size, hole positions and screen position come from Pimoroni's
dimensional drawing: 103 × 78 mm, with holes 3 mm in from each corner. The
active area is 84.6 × 56.4 mm, 7.1 mm from the buttons' edge and 10.8 mm
from the bottom.
