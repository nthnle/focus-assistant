# Inky stand

A printable desk stand for the Inky Impression 4.0" (Spectra 6) e-ink panel:
a thin frame, 4 mm deep, leaning 20° back on a foot that slides out, so the
two pack flat to travel.

<img alt="The stand with the panel in it, from the front" src="inky-stand.png" width="600">

- **Only the screen shows.** The window is the size of the active area,
  with an even 13 mm border and a slightly wider bottom, as framers do. The
  frame sits 2 mm in front of the board, so it covers the glass's edge and
  the display's own ribbon without touching them; a lip behind its edge
  hides that gap, so from the side it's one thin plate.
- **Only the four corners touch the board.** The panel screws on from
  behind, through its M2 corner holes into posts under the frame. The back
  stays open, so the Hack3r (or a ribbon plug) and the buttons on the back
  are free, and a screwdriver reaches every screw straight on.
- **The foot slides in and out.** It's a U: two arms joined at the back,
  open at the front so the ribbon to the Pi drops straight down. The arms
  are dovetails that slide into two sockets behind the frame's bottom edge.
  They set the lean, and the foot comes with the stand when you lift it;
  pull the foot straight back to take it off.

<img alt="The stand from behind: the panel, the electronics behind it, and the U-shaped foot in its two sockets" src="inky-stand-back.png" width="600">

## Print

Two parts, both ready to print as they are, with no supports:

| File | Lies on | Size |
|---|---|---|
| `inky-stand-frame.stl` | its front face, the sockets rising from it | 112 × 88 mm, 19 mm tall |
| `inky-stand-foot.stl` | its top, upside down | 81 × 68 mm, 4 mm tall |

<img alt="The two parts flat: the frame face down with its two sockets, and the U-shaped foot" src="inky-stand-flat.png" width="600">

- PLA or PETG, 0.2 mm layers, 3 walls, 20% infill. Matte black matches the
  panel's black border.
- **Try the fit before anything else:** slide the foot into the sockets. It
  should go in with a firm push and not wobble. If it won't go in, set
  `fit` to `0.3` and print the foot again (only the foot); if it's loose,
  `0.1`. Sanding the arms' sides works for a small difference.

## Put it together

1. Lay the frame face down, with the foot out. Lay the panel face down in
   it: its corner holes sit over the four posts.
2. Four **M2 × 5 mm** screws, from behind. They cut their own thread, so stop
   when they're snug. A longer screw would come out of the front.
3. Plug the ribbon back in.
4. Slide the foot's arms into the sockets until they stop, and stand it up.

**To travel:** pull the foot straight out backwards. The frame (with the
panel in it) and the foot lie flat.

**Pressing the buttons:** the stand is light, so a one-finger push on a
button from behind can tip it forward. Pinch instead, with your thumb on the
front and your finger on the button, or stick the foot down with museum gel.

## Change it

`inky-stand.scad` is the source. Open it in [OpenSCAD](https://openscad.org),
change the settings at the top (the Customizer lists them), then export with
**Render** (F6) and **Export as STL**: once with `layout = "frame"`, once
with `layout = "foot"`. `layout = "desk"` shows the stand upright, with the
board and the electronics in place to check the fit; `"flat"` shows both
parts as they print.

| Setting | Default | |
|---|---|---|
| `tilt` | `20` | Degrees back from upright. Print both parts again after changing it |
| `fit` | `0.2` | Gap around each arm in its socket. More if the foot won't go in, less if it's loose |
| `buttons_side` | `left` | As you face the screen. Set `right` if your buttons and the screen's wider margin are on the other side |
| `glass_clear` | `2` | Gap between the board's face and the frame. Raise it if the frame touches the glass |
| `window_margin` | `1` | How much bigger than the screen the window is, each side |
| `face_t` | `2` | The frame's thickness in front. Thicker is stiffer; add the same to the screw length |
| `chin` | `4` | How much wider the bottom border is. The sockets join the frame there, so keep it at 3 or more |
| `pilot_d` | `1.7` | Screw holes. Use `1.6` if the screws are loose, `1.8` if they're hard to turn |
| `foot_depth` | `70` | How far the foot reaches back |

The board's size, hole positions and screen position come from Pimoroni's
dimensional drawing: 103 × 78 mm, with holes 3 mm in from each corner. The
active area is 84.6 × 56.4 mm, 7.1 mm from the buttons' edge and 10.8 mm
from the bottom.
