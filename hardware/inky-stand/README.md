# Inky stand

A printable desk stand for the Inky Impression 4.0" (Spectra 6) e-ink panel.
It holds the panel 20° back from upright, in a thin frame.

<img alt="The stand with the panel in it, from the front" src="inky-stand.png" width="600">

- **Only the four corners touch the board.** The panel screws on from
  behind through its M2 corner holes into the frame's corner tabs. The
  screen starts 7 mm in from the edge, so the tabs miss it. Everywhere else
  the frame stays outside the board's outline, so whatever is on the back
  can't hit it, whether that's the Mini Black HAT Hack3r or just a ribbon
  plug.
- **The buttons' side is open**, so A to D can be pressed from the side or
  behind.
- **The other side leaves 4 mm** for the display's own ribbon, which bends
  around that edge, and carries the brace that holds the frame up.
- **The middle of the base is open**, so the ribbon to the Pi can drop
  straight down.

## Print

`inky-stand.stl` is ready to print as it is: it lies on the frame's front
face, with the base and the brace rising from it, leaning at most 20° past
vertical. No supports are needed.

- PLA or PETG, 0.2 mm layers, 3 walls, 20% infill
- About 122 × 97 mm on the bed and 79 mm tall

## Put it together

1. Four **M2 × 6 mm** screws, from behind, through the board's corner holes
   into the tabs. They cut their own thread, so stop when they're snug. A
   longer screw would come out of the front.
2. Plug the ribbon back in and let it drop through the base.

## Change it

`inky-stand.scad` is the source. Open it in [OpenSCAD](https://openscad.org),
change the settings at the top (the Customizer lists them), then export a new
STL with **Render** (F6) and **Export as STL**. `layout = "desk"` shows the stand
upright, with the board in place to check the fit.

| Setting | Default | |
|---|---|---|
| `tilt` | `20` | Degrees back from upright |
| `buttons_side` | `left` | The open side, as you face the screen. Set `right` if your buttons are on the other side |
| `ribbon_gap` | `4` | Room for the display's ribbon around the far edge |
| `pilot_d` | `1.7` | Screw holes. Use `1.6` if the screws are loose, `1.8` if they're hard to turn |
| `base_depth` | `75` | Front to back |

The board's size and hole positions come from Pimoroni's dimensional
drawing: 103 × 78 mm, with holes 3 mm in from each corner.
