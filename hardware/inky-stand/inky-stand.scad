// A desk stand for the Pimoroni Inky Impression 4.0" (Spectra 6), in two
// parts: a slim bumper around the board, and a foot that slides into it and
// out again, so the two pack flat to travel.
//
// The bumper covers nothing on the front: the panel's own thin black border,
// its glass and its print all show, inside a rim `wall` wide, flush with the
// board's face. The board drops in from the front and screws through its four
// M2 corner holes into pads behind its corners, so the screw heads show at the
// corners as on the bare board. Nothing else touches it: the back stays open,
// so the Hack3r and the buttons on the back are free.
//
// The foot is a U: two arms, joined at the back, open at the front for the
// cable to the Pi. The arms are dovetails that slide into two sockets behind
// the board's bottom edge, which set the lean (`tilt`) and keep the foot from
// dropping out when you lift the stand; it pulls straight out backwards. A
// sloped rail behind the board's bottom edge joins the sockets to the rim, so
// the bottom of the rim is as slim as the rest.
//
// Measured: the board is 103 x 78 mm (Pimoroni's drawing) with holes 3 mm in
// from each corner; the screen stands under 1.5 mm proud of it; the Hack3r on
// the back starts 7.6 mm up from the bottom edge, and nothing else on the back
// is lower than that.
//
// Printing: `layout = "frame"` lies on its front, with the pads and sockets
// rising from it; `layout = "foot"` lies flat, upside down. `layout = "desk"`
// shows them together, standing; `"flat"` both, as they print and pack.
// Screws: four M2 x 5 mm, from the front; they cut their own thread.

/* [Panel] */
board_w = 103;
board_h = 78;
board_t = 1.6;
board_r = 3;                  // the board's corner radius
hole_inset = 3;               // corner hole centres, from both edges
buttons_side = "left";        // as you look at the screen

/* [Bumper] */
gap = 0.5;                    // clearance around the board
wall = 2;                     // the rim's width, all round
rim_back = 2;                 // how far the rim reaches behind the board
pad = 7.5;                    // the corner pads behind the board, from its corner
pad_t = 4;                    // their thickness: what the screws bite into (and the bottom rail's)
chamfer = 0.6;                // on the rim's front edge
pilot_d = 1.7;                // M2 self-tapping into PLA/PETG

/* [Stand] */
layout = "frame";             // "frame" or "foot" (the parts to print), "desk" (standing) or "flat" (both, packed)
tilt = 20;                    // leans back this far from upright
foot_depth = 70;              // front to back
foot_t = 3;                   // the foot's thickness
foot_arm = 12;                // width of each arm, at its top
arm_inset = 14;               // each arm's centre, in from the board's side
dovetail = 20;                // degrees each arm's sides lean in, towards the desk (what holds the foot on)
foot_bar = 12;                // the bar joining the arms at the back
socket_len = 12;              // how far the arms go into the frame (longer would reach the Hack3r)
socket_front = 2;             // the wall the arms stop against
socket_wall = 2.4;
socket_roof = 1.6;
fit = 0.2;                    // gap around each arm in its socket: more if it won't go in, less if loose

$fn = 64;

holes = [for (x = [hole_inset, board_w - hole_inset], y = [hole_inset, board_h - hole_inset]) [x, y]];

// --- panel space: x right, y up, z towards you. The board's front is at
// z = 0 (the rim's front too), its back at -board_t.

back_z = -board_t;
rim_z = back_z - rim_back;    // the rim's back
pad_z = back_z - pad_t;       // the pads' and the rail's back: the deepest part

inner = [-gap, -gap, board_w + gap, board_h + gap];
outer = [inner[0] - wall, inner[1] - wall, inner[2] + wall, inner[3] + wall];

module rrect(r4, r) {
    // A rounded rectangle from [x0, y0, x1, y1].
    translate([r4[0] + r, r4[1] + r]) offset(r) square([r4[2] - r4[0] - 2 * r, r4[3] - r4[1] - 2 * r]);
}

module outline() { rrect(outer, board_r + gap + wall); }
module opening() { rrect(inner, board_r + gap); }

module rim() {
    // Around the board's edge, flush with its face, with the front edge chamfered.
    difference() {
        hull() {
            translate([0, 0, rim_z]) linear_extrude(-rim_z - chamfer) outline();
            translate([0, 0, -chamfer]) linear_extrude(chamfer) offset(-chamfer) outline();
        }
        translate([0, 0, rim_z - 1]) linear_extrude(-rim_z + 2) opening();
    }
}

module pads() {
    // Behind each corner, the board's back rests on a pad, and its screw bites
    // into it. Printed face down, a pad is a short bridge across the corner,
    // held by the rim on two sides.
    for (h = holes) {
        cx = h[0] < board_w / 2 ? outer[0] : outer[2];
        cy = h[1] < board_h / 2 ? outer[1] : outer[3];
        intersection() {
            translate([0, 0, pad_z]) linear_extrude(-pad_z) outline();
            hull() {
                translate([h[0], h[1], pad_z]) cylinder(d = 2 * hole_inset + 1, h = pad_t);
                translate([min(cx, h[0]), min(cy, h[1]), pad_z])
                    cube([abs(cx - h[0]), abs(cy - h[1]), pad_t]);
            }
        }
    }
}

module rail() {
    // Behind the board's bottom edge, the length of the rim: it joins the
    // sockets to the rim. Its face towards the board slopes at 45° (up to
    // 3.5 mm behind the board, clear of the Hack3r at 7.6), so it prints face
    // down without supports and never touches the board's back.
    intersection() {
        translate([0, 0, pad_z]) linear_extrude(-pad_z) outline();
        hull() {
            translate([outer[0], outer[1], pad_z]) cube([outer[2] - outer[0], inner[1] + pad_t - outer[1], 0.01]);
            translate([outer[0], outer[1], back_z - 0.01]) cube([outer[2] - outer[0], inner[1] - outer[1], 0.01]);
        }
    }
}

module frame() {
    difference() {
        union() {
            rim();
            pads();
            rail();
        }
        for (h = holes) translate([h[0], h[1], pad_z - 1]) cylinder(d = pilot_d, h = -pad_z + 1);
    }
}

// --- standing it up: panel y becomes up and panel z towards you (-y),
// leaning back by `tilt`. The rail's back bottom edge rests on the desk.

drop = -(outer[1] * cos(tilt) + pad_z * sin(tilt));

module stood() {
    translate([0, 0, drop]) rotate([-tilt, 0, 0]) rotate([90, 0, 0]) children();
}

module unstood() {
    rotate([-90, 0, 0]) rotate([tilt, 0, 0]) translate([0, 0, -drop]) children();
}

// Where the frame's back bottom edge meets the desk, in desk coordinates.
heel_y = outer[1] * sin(tilt) - pad_z * cos(tilt);

arm_x = [arm_inset, board_w - arm_inset];
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
    // The sockets keep out of the opening, and behind the board they stay
    // under the rail's 45° face: they merge into the rail, never come near the
    // board, and leave no flat ledge to print over air.
    stood() {
        translate([outer[0] - 1, inner[1], back_z]) cube([outer[2] - outer[0] + 2, board_h + 2 * gap, 10]);
        hull() {
            translate([outer[0] - 1, inner[1], back_z]) cube([outer[2] - outer[0] + 2, board_h + 2 * gap, 0.01]);
            translate([outer[0] - 1, inner[1] + 50, back_z - 50]) cube([outer[2] - outer[0] + 2, board_h + 2 * gap, 0.01]);
        }
    }
}

module sockets() {
    // A block for each arm, joined to the rail behind the board's bottom edge,
    // with a dovetail slot open at the back and at the desk.
    w = foot_arm + 2 * (fit + socket_wall);
    difference() {
        union() for (x = arm_x) hull() {
            stood() translate([x - w / 2, outer[1], pad_z]) cube([w, inner[1] - outer[1], -pad_z]);
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
        // Nothing in front of the rim's face, so it lies flat to print.
        stood() translate([outer[0] - 50, outer[1] - 100, -200]) cube([outer[2] - outer[0] + 100, 400, 200]);
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
        // The board (solid, as it is), its screen, and the Hack3r behind it
        // (see-through, as measured), to check the fit. Never part of an export.
        color("dimgray") stood() translate([0, 0, back_z]) cube([board_w, board_h, board_t]);
        color("whitesmoke") stood() translate([3, 7, 0]) cube([92, 64, 1.4]);
        %stood() translate([8, 7.6, back_z - 16]) cube([75, 65.4, 16]);
    } else if (layout == "flat") {
        // Both parts as they print, and pack.
        rotate([180, 0, 0]) unstood() frame_standing();
        translate([0, -outer[3] - 15, foot_t]) rotate([180, 0, 0]) translate([0, -heel_y, 0]) foot();
    } else if (layout == "foot") {
        // Upside down: the arms' wide tops on the bed, their sides leaning in.
        translate([0, 0, foot_t]) rotate([180, 0, 0]) translate([0, -heel_y, 0]) foot();
    } else {
        rotate([180, 0, 0]) unstood() frame_standing();
    }
}
