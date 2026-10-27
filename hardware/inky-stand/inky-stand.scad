// A desk stand for the Pimoroni Inky Impression 4.0" (Spectra 6).
//
// A frame with an even border and a window the size of the screen's active
// area, leaning back on a slim foot. The frame sits a little in front of the
// board, so it covers the glass's edge and the display's ribbon without
// touching them. The board screws on from behind through its four M2 corner
// holes into posts under the frame; nothing else touches it, and the back
// stays open, so the Hack3r and the buttons on the back are free. The
// foot is two arms, open between them for the cable to the Pi.
//
// Dimensions from Pimoroni's drawing: board 103 x 78 mm, holes 3 mm in from
// each corner, active area 84.6 x 56.4 mm starting 7.1 mm from the buttons'
// edge and 10.8 mm from the bottom.
//
// Printing: `layout = "print"` (the default, what the STL is) lies on the
// frame's front face; the rest rises from it, leaning at most `tilt` past
// vertical, so it needs no supports. `layout = "desk"` shows it standing.
// Screws: four M2 x 6 mm, from behind; they cut their own thread.

/* [Panel] */
board_w = 103;
board_h = 78;
board_t = 1.6;
hole_inset = 3;               // corner hole centres, from both edges
active = [7.1, 10.8, 84.6, 56.4];  // x, y, width, height, from the bottom corner on the buttons' side
buttons_side = "left";        // as you look at the screen

/* [Frame] */
window_margin = 1;            // the window is this much bigger than the active area, each side
glass_clear = 2;              // gap between the board's face and the frame: room for the glass
face_t = 3.2;                 // the frame's face
wall = 2;                     // the rim around the board's edge
gap = 1;                      // clearance around the board
ribbon_gap = 3.5;             // clearance on the display ribbon's side (it bends around the edge)
corner_r = 6;
window_r = 1.5;
chamfer = 0.8;                // on the front edges
post_d = 5.5;
pilot_d = 1.7;                // M2 self-tapping into PLA/PETG
skin = 0.6;                   // left in front of the screw holes

/* [Stand] */
layout = "print";             // "print" or "desk"
tilt = 20;                    // leans back this far from upright
foot_depth = 70;              // front to back
foot_t = 5;
foot_arm = 14;                // width of each of the foot's two arms

$fn = 64;

holes = [for (x = [hole_inset, board_w - hole_inset], y = [hole_inset, board_h - hole_inset]) [x, y]];

// --- panel space: x right, y up, z towards you. The board's front is at
// z = 0, its back at -board_t; the frame's face runs from glass_clear to front_z.

front_z = glass_clear + face_t;
back_z = -board_t;

win = [active[0] - window_margin, active[1] - window_margin,
       active[0] + active[2] + window_margin, active[1] + active[3] + window_margin];
// One border width all round: as narrow as the far side (board, ribbon gap, rim) allows.
border = max(board_w + ribbon_gap + wall - win[2], win[0] + gap + wall,
             board_h + gap + wall - win[3], win[1] + gap + wall);
outer = [win[0] - border, win[1] - border, win[2] + border, win[3] + border];

module rrect(r4, r) {
    // A rounded rectangle from [x0, y0, x1, y1].
    translate([r4[0] + r, r4[1] + r]) offset(r) square([r4[2] - r4[0] - 2 * r, r4[3] - r4[1] - 2 * r]);
}

module grow(r4, d) { rrect([r4[0] - d, r4[1] - d, r4[2] + d, r4[3] + d], corner_r); }

module face() {
    // The front, with its outer edge and the window's edge chamfered.
    difference() {
        hull() {
            translate([0, 0, glass_clear]) linear_extrude(face_t - chamfer) rrect(outer, corner_r);
            translate([0, 0, front_z - chamfer]) linear_extrude(chamfer) offset(-chamfer) rrect(outer, corner_r);
        }
        hull() {
            translate([0, 0, glass_clear - 1]) linear_extrude(face_t - chamfer + 1) rrect(win, window_r);
            translate([0, 0, front_z - 0.01]) linear_extrude(0.02) offset(chamfer) rrect(win, window_r);
        }
    }
}

module rim() {
    // Around the board's edge, back to its rear face, so it looks solid from the side.
    translate([0, 0, back_z]) linear_extrude(glass_clear - back_z) difference() {
        rrect(outer, corner_r);
        translate([-gap, -gap]) square([board_w + gap + ribbon_gap, board_h + 2 * gap]);
    }
}

module frame() {
    difference() {
        union() {
            face();
            rim();
            for (h = holes) translate([h[0], h[1], 0]) cylinder(d = post_d, h = glass_clear + 0.01);
        }
        for (h = holes) translate([h[0], h[1], back_z - 1]) cylinder(d = pilot_d, h = front_z - skin - back_z + 1);
    }
}

// --- standing it up: panel y becomes up and panel z towards you (-y),
// leaning back by `tilt`. The frame's back bottom edge rests on the desk.

drop = -back_z * sin(tilt) - outer[1] * cos(tilt);

module stood() {
    translate([0, 0, drop]) rotate([-tilt, 0, 0]) rotate([90, 0, 0]) children();
}

module unstood() {
    rotate([-90, 0, 0]) rotate([tilt, 0, 0]) translate([0, 0, -drop]) children();
}

// Where the frame's back meets the desk, in desk coordinates.
heel_y = -back_z * cos(tilt) + outer[1] * sin(tilt);

module foot() {
    // Two slim arms reaching back, open between them for the cable. (A closed
    // ring would need its back edge printed across the gap, in mid-air.)
    // Square at the front, where they meet the frame; rounded at the back.
    for (x = [outer[0], outer[2] - foot_arm]) linear_extrude(foot_t) hull() {
        translate([x, heel_y]) square([foot_arm, 1]);
        translate([x + foot_arm / 2, heel_y + foot_depth - foot_arm / 2]) circle(d = foot_arm);
    }
    // The frame's bottom edge, filled down to the foot, so the two are one piece.
    hull() {
        stood() translate([0, 0, back_z]) linear_extrude(front_z - back_z)
            rrect([outer[0], outer[1], outer[2], outer[1] + 2 * corner_r], corner_r);
        translate([0, heel_y, 0]) linear_extrude(0.01)
            rrect([outer[0], 0, outer[2], 12], corner_r);
    }
}

module standing() {
    intersection() {
        union() {
            stood() frame();
            foot();
        }
        // Nothing in front of the frame's face, so it lies flat to print.
        stood() translate([outer[0] - 50, outer[1] - 100, back_z - 200]) cube([outer[2] - outer[0] + 100, 400, front_z - back_z + 200]);
    }
}

module side(flip) {
    if (flip) translate([board_w, 0, 0]) mirror([1, 0, 0]) children();
    else children();
}

side(buttons_side == "right") {
    if (layout == "desk") {
        standing();
        // The board and the Hack3r stack behind it, to check the fit (preview only).
        %stood() translate([0, 0, back_z]) cube([board_w, board_h, board_t]);
        %stood() translate([13, 16, back_z - 35]) cube([65, 56, 35]);
    } else {
        translate([0, 0, front_z]) rotate([180, 0, 0]) unstood() standing();
    }
}
