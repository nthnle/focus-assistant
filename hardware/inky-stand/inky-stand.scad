// A desk stand for the Pimoroni Inky Impression 4.0" (Spectra 6), in two
// parts: a thin frame, and a foot that slides into it and out again, so the
// two pack flat to travel.
//
// The frame has an even border and a window the size of the screen's active
// area. It sits a little in front of the board, so it covers the glass's
// edge and the display's ribbon without touching them; a short lip behind its
// edge hides that gap from the side, so the frame reads as one 4 mm plate.
// The board screws on from behind through its four M2 corner holes into
// posts under the frame; nothing else touches it, the back stays open (the
// Hack3r and the buttons on the back are free), and a screwdriver has a clear
// path to every hole.
//
// The foot is a U: two arms, joined at the back, open at the front for the
// cable to the Pi. The arms are dovetails that slide into two sockets behind
// the frame's bottom edge, which set the lean (`tilt`) and keep the foot from
// dropping out when you lift the stand; it pulls straight out backwards.
//
// Dimensions from Pimoroni's drawing: board 103 x 78 mm, holes 3 mm in from
// each corner, active area 84.6 x 56.4 mm starting 7.1 mm from the buttons'
// edge and 10.8 mm from the bottom.
//
// Printing: `layout = "frame"` lies on its front face, with the sockets
// rising from it; `layout = "foot"` lies flat, upside down. Neither needs
// supports. `layout = "desk"` shows them together, standing.
// Screws: four M2 x 5 mm, from behind; they cut their own thread.

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
face_t = 2;                   // the frame's face
lip = 1.2;                    // the lip behind the face's edge, where there's room for one
gap = 1;                      // clearance around the board
overlap = 1;                  // how far the face reaches past the board's edge, to hide it
ribbon_gap = 2.5;             // on the display ribbon's side: it bends around the edge
chin = 4;                     // the bottom border is this much wider than the others
corner_r = 6;
window_r = 1.5;
chamfer = 0.6;                // on the front edges
post_d = 5.5;
pilot_d = 1.7;                // M2 self-tapping into PLA/PETG
skin = 0.6;                   // left in front of the screw holes

/* [Stand] */
layout = "frame";             // "frame" or "foot" (the parts to print), "desk" (standing) or "flat" (both, packed)
tilt = 20;                    // leans back this far from upright
foot_depth = 70;              // front to back
foot_t = 4;                   // the foot's thickness
foot_arm = 12;                // width of each arm, at its top
dovetail = 15;                // degrees each arm's sides lean in, towards the desk
foot_bar = 12;                // the bar joining the arms at the back
socket_len = 16;              // how far the arms go into the frame
socket_front = 2;             // the wall the arms stop against
socket_wall = 2.4;
socket_roof = 1.6;
fit = 0.2;                    // gap around each arm in its socket: more if it won't go in, less if loose
driver_d = 9;                 // kept clear behind each screw hole, for a screwdriver

$fn = 64;

holes = [for (x = [hole_inset, board_w - hole_inset], y = [hole_inset, board_h - hole_inset]) [x, y]];

// --- panel space: x right, y up, z towards you. The board's front is at
// z = 0, its back at -board_t; the frame's face runs from glass_clear to front_z.

front_z = glass_clear + face_t;
back_z = -board_t;

win = [active[0] - window_margin, active[1] - window_margin,
       active[0] + active[2] + window_margin, active[1] + active[3] + window_margin];
// One border width all round: as narrow as the widest side allows, which is
// the ribbon's (the board, the ribbon around its edge).
border = max(board_w + ribbon_gap - win[2], win[0] + overlap,
             board_h + overlap - win[3], win[1] + overlap);
// The bottom is `chin` wider, as framers do (it looks centred to the eye):
// that strip below the board is where the foot joins the frame.
outer = [win[0] - border, win[1] - border - chin, win[2] + border, win[3] + border];

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
    // A lip behind the face's edge, across the gap in front of the board, so
    // the frame looks like one plate from the side. Only where it clears the
    // board (and none on the ribbon's side).
    linear_extrude(glass_clear) difference() {
        rrect(outer, corner_r);
        offset(-lip) rrect(outer, corner_r);
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

// The frame's back face (the lip and the posts) is at z = 0.
drop = -outer[1] * cos(tilt);

module stood() {
    translate([0, 0, drop]) rotate([-tilt, 0, 0]) rotate([90, 0, 0]) children();
}

module unstood() {
    rotate([-90, 0, 0]) rotate([tilt, 0, 0]) translate([0, 0, -drop]) children();
}

// Where the frame's back meets the desk, in desk coordinates.
heel_y = outer[1] * sin(tilt);

// The arms, centred as close to the corners as a screwdriver at the bottom
// holes allows.
arm_off = hole_inset + driver_d / 2 + 1 + socket_wall + fit + foot_arm / 2;
arm_x = [arm_off, board_w - arm_off];
arm_bottom = foot_arm - 2 * foot_t * tan(dovetail);

module dove(x, extra = 0) {
    // An arm's cross-section (x across, z up), `extra` bigger all round.
    polygon([[x - arm_bottom / 2 - extra, -extra], [x + arm_bottom / 2 + extra, -extra],
             [x + foot_arm / 2 + extra, foot_t + extra], [x - foot_arm / 2 - extra, foot_t + extra]]);
}

module along(y0, y1) {
    // Extrude a cross-section in the x-z plane from y0 to y1.
    translate([0, y1, 0]) rotate([90, 0, 0]) linear_extrude(y1 - y0) children();
}

module clear_of_board() {
    // 1 mm round the board's bottom edge, back and front, and a screwdriver's
    // path behind each hole.
    stood() translate([-gap, -gap, back_z - 1])
        cube([board_w + 2 * gap + ribbon_gap, board_h + 2 * gap, glass_clear + 1 - back_z]);
    // Below the board's bottom edge the cut slopes at 45° rather than leaving a
    // flat ledge, which would print as an unsupported ceiling.
    stood() hull() {
        translate([-gap, -gap, back_z - 1]) cube([board_w + 2 * gap + ribbon_gap, 6, 0.01]);
        translate([-gap, -gap, back_z - 7]) cube([board_w + 2 * gap + ribbon_gap, 0.01, 0.01]);
    }
    stood() for (h = holes) translate([h[0], h[1], back_z - 100]) cylinder(d = driver_d, h = 100);
}

module sockets() {
    // A block behind the frame's bottom edge for each arm, joined to the strip
    // below the board (into the face, not just up to it: touching faces
    // wouldn't fuse), with a dovetail slot open at the back and at the desk.
    w = foot_arm + 2 * (fit + socket_wall);
    difference() {
        union() for (x = arm_x) hull() {
            stood() translate([x - w / 2, outer[1], 0]) cube([w, -gap - outer[1], glass_clear + face_t / 2]);
            translate([x - w / 2, heel_y, 0]) cube([w, socket_len, foot_t + fit + socket_roof]);
        }
        for (x = arm_x) along(heel_y + socket_front, heel_y + socket_len + 1) dove(x, fit);
        // The slot's open bottom: the arm's narrow underside (and the fit) gets out of the way.
        for (x = arm_x) along(heel_y + socket_front, heel_y + socket_len + 1)
            translate([x - arm_bottom / 2 - fit, -5]) square([arm_bottom + 2 * fit, 5 + fit]);
        clear_of_board();
    }
}

module foot() {
    // In place: the arms against the sockets' front walls.
    y0 = heel_y + socket_front + fit;
    y1 = heel_y + foot_depth;
    for (x = arm_x) along(y0, y1) dove(x);
    translate([arm_x[0], y1 - foot_bar, 0]) cube([arm_x[1] - arm_x[0], foot_bar, foot_t]);
}

module frame_standing() {
    intersection() {
        union() {
            stood() frame();
            sockets();
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
        frame_standing();
        foot();
        // The board (solid, as it is) and the Hack3r stack behind it (see-through),
        // to check the fit. Only for looking: never part of an export.
        color("dimgray") stood() translate([0, 0, back_z]) cube([board_w, board_h, board_t]);
        %stood() translate([13, 16, back_z - 35]) cube([65, 56, 35]);
    } else if (layout == "flat") {
        // Both parts as they print, and pack.
        translate([0, 0, front_z]) rotate([180, 0, 0]) unstood() frame_standing();
        translate([0, -outer[3] - 15, foot_t]) rotate([180, 0, 0]) translate([0, -heel_y, 0]) foot();
    } else if (layout == "foot") {
        // Upside down: the arms' wide tops on the bed, their sides leaning in.
        translate([0, 0, foot_t]) rotate([180, 0, 0]) translate([0, -heel_y, 0]) foot();
    } else {
        translate([0, 0, front_z]) rotate([180, 0, 0]) unstood() frame_standing();
    }
}
