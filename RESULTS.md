# Keeping test card results: plan

A test card answers a question once. Photographed and filed with what produced
it, it answers that question every time the same material comes back.

This is a plan. Nothing here is built.

## What the problem actually is

The cards now burn their own axis labels, so a card lifted off the machine still
says which cell was which. What it does not say is what it was cut on: three
millimetre birch or five, the sheet from the good supplier or the damp one, what
the beam offset was that day, which firmware. Six weeks later the card is a piece
of scrap with numbers on it and no context, and the settings get found again from
scratch.

So the unit worth storing is not the photograph. It is the photograph together
with the card that produced it and the material it was cut on.

## What a stored result holds

- **The card design**, exactly as sent — the speeds, powers, passes, interval and
  cell size. It is already a design file, so store it as one.
- **The photograph**, or several: cards read differently in raking light than
  flat light, and both are worth keeping.
- **The material**, in the operator's own words: what it is, how thick, where it
  came from. Free text beats a taxonomy nobody fills in honestly.
- **The machine state** the app already knows: firmware version, beam offset,
  the profile settings, the app version. All of it is on hand at send time and
  none of it will be remembered later.
- **The verdict**: which cell won, and why in a sentence. This is the part that
  turns a photograph into an answer.

Date and a generated id come free.

## Where it lives

Beside the material library, in the same state directory, because it is the same
kind of thing: something learned about a material that should outlive the design
it was learned from.

One folder per result, holding the design, the photographs and a small JSON
manifest. A folder per result rather than one big index means a result can be
copied to another machine, or sent to someone, by copying a folder. It also
means a corrupt manifest costs one result rather than all of them.

Photographs are the only large thing here. Store them re-encoded as JPEG with a
long edge of about 2000 pixels: enough to see whether a cell cut through, small
enough that a hundred results do not fill a disk. Keep the original if the
operator asks, but do not do it by default.

## How the photograph gets in

**Now:** the file dialog, or dropped onto the window, which already works for
every other kind of file.

**Later, with the bed camera:** the card is on the bed and the camera is
calibrated to it, so the app can take the picture itself when the job finishes.
That is the point at which this stops being filing and starts being useful,
because the app then knows where each cell is in the photograph.

Which leads to the thing worth building the whole feature for:

## Cropping each cell

With a calibrated camera, the app knows every cell's position in bed
millimetres, and the mapping from bed millimetres to image pixels. So it can cut
the photograph into one thumbnail per cell and file each one against the exact
speed and power that produced it.

That turns the library from "a photo of a card" into something a person can
search: show me every result at 1200 mm/min on birch, or show me what 40 percent
power looks like across every material tried. It is also how a material preset
earns a picture beside it: this is what that setting looks like on that stuff.

Without the camera the same thing can be done by hand — click the four corners
of the card in the photograph, which gives the same mapping for that one image.
Worth building that way first, because it works today and it is the fallback
when the camera cannot see the whole bed.

## What it changes elsewhere

- **Material presets** gain an optional link to the result that justified them,
  so a preset stops being four numbers with no provenance.
- **The card generator** should record the card it made into the pending result,
  so filing a photograph later does not require retyping the settings.
- **Nothing in the machine path changes.** This is all after the fact.

## Order to build it

1. The store: folders, manifest, save and load, with tests. No interface.
2. Filing a result by hand: pick a photo, describe the material, pick the winning
   cell. This is already useful.
3. Corner-click mapping, and per-cell thumbnails from it.
4. A browser: results by material, by setting, by date.
5. Camera capture, once the camera exists, replacing the corner clicking.
6. Presets that carry the result that produced them.

Stop after 2 if it turns out the photographs get taken and never looked at
again. That is the honest risk here: this is a filing system, and filing systems
are easy to build and easy to abandon. Two things would tell us early that it is
being used: results getting a verdict written on them, and presets being created
from results rather than from the panel.

## What could make it not worth doing

- **The photographs may not be readable enough.** A phone photo of scorched wood
  under a garage light may not show the difference between two adjacent cells,
  which is exactly the difference being looked for. Test this with a real card
  and a real phone before building step 3.
- **It competes with a notebook.** A pencil line on the card itself is free and
  works. This is only worth building if it does something the card cannot: being
  searchable, and sitting next to the presets it justifies.
