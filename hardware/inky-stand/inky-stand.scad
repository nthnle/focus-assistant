// A desk stand for the Pimoroni Inky Impression 4.0" (Spectra 6).
//
// A thin frame around the panel, leaning back on a base. The panel screws
// on from behind through its four M2 corner holes into the frame's corner
// tabs; the tabs cover only the corners, which are clear (the screen starts
// 7 mm in). Everywhere else the frame stays outside the board's outline, so
// whatever is on the back (the Hack3r, a ribbon plug) can't hit it. The
// buttons' side is left open, and the other side leaves room for the
// display's ribbon, which bends around that edge.
//
// Dimensions from Pimoroni's drawing: board 103 x 78 mm, holes 3 mm in from
// each corner, buttons on the left edge 17.85-60.15 mm up from the bottom.
//
// Printing: `layout = "print"` (the default, what the STL is) lies on the
// frame's front face; everything else rises from it, leaning at most `tilt`
// past vertical, so it needs no supports. `layout = "desk"` shows it standing.
// Screws: four M2 x 6 mm, from behind; they cut their own thread.

/* [Panel] */
board_w = 103;
board_h = 78;
board_t = 1.6;
hole_inset = 3;         // corner hole centres, from both edges
buttons_side = "left";  // as you look at the screen; this side stays open

/* [Stand] */
layout = "print";       // "print" or "desk"
tilt = 20;              // leans back this far from upright
frame_t = 5;            // frame thickness; the screws go 4.4 mm into it
rail_w = 7;             // frame rail width
gap = 1;                // clearance around the board
ribbon_gap = 4;         // clearance on the display ribbon's side (it bends around the edge)
tab_d = 6.5;            // corner tabs; the screen starts 7 mm in
pilot_d = 1.7;          // M2 self-tapping into PLA/PETG
pilot_depth = 4.6;      // blind, from behind: leaves a skin on the front
base_t = 4;
base_depth = 75;        // front to back
arm_w = 16;             // the base's side arms; the middle is open for the cable
front_bar = 18;         // the base's front strip, under the frame
lift = 3;               // the frame's lowest edge sits this high off the desk

$fn = 48;

holes = [for (x = [hole_inset, board_w - hole_inset], y = [hole_inset, board_h - hole_inset]) [x, y]];

// --- panel space: x right, y up, z towards you. The board's front is at
// z = 0 and its back at -board_t; the frame lies in front, from 0 to frame_t.

x_lo = -gap - rail_w;                   // the open (buttons') side has no rail
x_hi = board_w + ribbon_gap + rail_w;
y_lo = -gap - rail_w;
y_hi = board_h + gap + rail_w;

bottom_rail = [[x_lo, y_lo], [x_hi, y_lo], [x_hi, -gap], [x_lo, -gap]];
top_rail = [[x_lo, board_h + gap], [x_hi, board_h + gap], [x_hi, y_hi], [x_lo, y_hi]];
side_rail = [[board_w + ribbon_gap, y_lo], [x_hi, y_lo], [x_hi, y_hi], [board_w + ribbon_gap, y_hi]];

module slab(pts) {
    linear_extrude(frame_t) polygon(pts);
}

module corner_tab(h) {
    // From the rail over the board's corner to the hole, and no further.
    edge_y = h[1] < board_h / 2 ? -gap - 0.01 : board_h + gap + 0.01;
    linear_extrude(frame_t) hull() {
        translate(h) circle(d = tab_d);
        translate([h[0] - tab_d / 2, edge_y]) square([tab_d, 0.02]);
    }
}

module frame() {
    difference() {
        union() {
            slab(bottom_rail);
            slab(top_rail);
            slab(side_rail);
            for (h = holes) corner_tab(h);
        }
        for (h = holes) translate([h[0], h[1], -1]) cylinder(d = pilot_d, h = pilot_depth + 1);
    }
}

// --- standing it up: panel y becomes up and panel z towards you (-y),
// leaning back by `tilt`, with the frame's lowest edge `lift` off the desk.

drop = -y_lo * cos(tilt) - frame_t * sin(tilt);

module stood() {
    translate([0, 0, drop + lift]) rotate([-tilt, 0, 0]) rotate([90, 0, 0]) children();
}

module unstood() {
    rotate([-90, 0, 0]) rotate([tilt, 0, 0]) translate([0, 0, -drop - lift]) children();
}

module to_desk(pts) {
    // A part of the frame, filled straight down to the desk.
    hull() {
        stood() slab(pts);
        linear_extrude(0.01) projection() stood() slab(pts);
    }
}

module base() {
    y0 = -50;   // well in front; trimmed to the frame's front face below
    difference() {
        translate([x_lo, y0, 0]) cube([x_hi - x_lo, base_depth - y0, base_t]);
        translate([x_lo + arm_w, -(frame_t - 1) * cos(tilt) + front_bar, -1])
            cube([x_hi - x_lo - 2 * arm_w, base_depth, base_t + 2]);
    }
}

module standing() {
    intersection() {
        union() {
            stood() frame();
            to_desk(bottom_rail);
            to_desk(side_rail);
            base();
        }
        // Nothing in front of the frame's face, so it lies flat to print.
        stood() translate([x_lo - 50, y_lo - 100, -200]) cube([x_hi - x_lo + 100, 400, 200 + frame_t]);
    }
}

module side(flip) {
    if (flip) translate([board_w, 0, 0]) mirror([1, 0, 0]) children();
    else children();
}

side(buttons_side == "right") {
    if (layout == "desk") {
        standing();
        // The board, for checking the fit (preview only, not exported).
        %stood() translate([0, 0, -board_t]) cube([board_w, board_h, board_t]);
    } else {
        translate([0, 0, frame_t]) rotate([180, 0, 0]) unstood() standing();
    }
}
