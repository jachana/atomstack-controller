# Desktop design

Operate mode: one native Windows window for design, setup, and monitored jobs beside
the machine. No decorative assets, motion effects or remote resources.

The user delegated the Windows stack. The direction is a light workshop instrument:
connection above position, measured coordinates beside explicit motion gates, and
diagnostic evidence below. This prioritizes native control conventions and readable
state over the skill's web concept/composition workflow, which does not fit this
bounded Windows controller milestone.

Segoe UI provides native labels; Consolas is confined to measurements and logs.
Background #f5f7fb, foreground #172033, secondary #53627a; white plotting/log surfaces,
blue #155eef position and selection, red #dc2626 stop control. System focus and disabled
states remain visible. Base font 10 pt; section 12 pt; position 22 pt. Outer inset
24 px with 6–18 px group spacing. Minimum 1100 × 820; default 1440 × 980.

The plot preserves the 365:305 aspect ratio. Bottom-left app zero is shown only
after confirmed homing; before that an explicit empty state replaces the marker.
Simulator identity is persistent at the top. Every jog passes the same controller
guard even when its button is enabled.

Version 0.2 adds guarded step and speed selectors beside the jog pad. Controls keep
their visual state while a short status query is in flight, so periodic polling no
longer produces blinking. The controller still serializes any click behind that poll.

The geometry workspace is embedded in the main window: bed drawing dominates the left;
shape list and exact process values occupy a fixed inspector on the right. Teal marks
the selected geometry, while a dashed orange rectangle shows the exact 2 mm Frame
outline. Frame keeps the laser off; Send job streams only commands produced by the
bounded geometry generator. The controller verifies command syntax, coordinates,
feed, power, final endpoint, Idle state, and zero power.

Version 0.4 keeps creation in one workspace: shape and text tools form the first row;
Frame/job sending and the burn-test generator form the action row; saved material settings
have their own row. Text uses installed Windows fonts for the preview and flattened
font contours for sending. Burn-test labels are workspace annotations rather than
laser geometry, keeping each test cell's exposure unambiguous.

Version 0.7 uses persistent connection, status, and STOP controls above two clear tabs.
Design & Send opens first; Machine & Jog contains homing, click-to-jog, manual jogging,
and the filtered activity log. The teal Send button is the primary action, while the
red stop remains visible from either tab.

Version 0.8 turns Machine & Jog into a read-only navigator. Arrow keys accumulate a
bounded destination using the selected step, mouse-wheel zoom keeps the machine bed
legible, and design geometry appears in gray. The selected object is darker and its
bottom-left, bottom-right, top-left, top-right, and center points are direct guarded
jog destinations.

Version 0.9 makes Design & Send a conventional desktop vector workspace. A compact
command strip separates creation, history, viewport, placement, and machine actions.
The canvas sits on a pale drafting surface with a white material bed; blue selection
handles, dark geometry, and the orange Frame path form the visual state hierarchy.
Direct manipulation, grid snapping, keyboard shortcuts, exact values, alignment,
layer order, and project files all share the same validated Shape model.

Version 0.10 adds a separate read-only Job Preview window. A large white bed keeps the
path primary; future segments recede, completed travel uses dashed gray, and laser
segments interpolate from blue to red by power. The summary and scrubber explain job
cost and order without exposing raw G-code or contacting the controller.

Version 0.11 represents multi-selection with blue object outlines and one dashed blue
group boundary. Exact geometry stays anchored to a primary object in the inspector;
shared process values apply to the selected set. Group translation clamps the combined
bounds to the bed, preserving spacing while snapping the primary object to the grid.
