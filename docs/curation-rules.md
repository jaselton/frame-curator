# Curation rules

These are the rules the gates in `frame_curator/art.py` encode, and the standard you are expected to apply when you
look at the contact sheets. The gates shrink the pile; your eyes decide.

## Allowed themes
Monet water lilies and gardens (Giverny); Impressionist landscapes and city scenes; Dutch and Barbizon landscapes
and seascapes; still lifes (flowers, fruit); architecture; horses and countryside; calm pastoral and genre scenes.

## Hard-exclude (no exceptions in the default harvest)
- Religious narrative and martyrdom: Crucifixion, Pietà, Lamentation, saints, Last Judgment, Old Testament scenes
  (Hagar, Sodom, Abraham and Isaac, Judith, David and Goliath, Salome, John the Baptist).
- Any beheading or severed head.
- Battles, sieges, cavalry, soldiers, weapons in use, hunting kills.
- All mythological and allegorical scenes (Venus, Diana, nymphs, Europa, bacchanals, satyrs): they carry the nudity.
- Any nude or partial nudity, including bathers, a shirtless fisherman, a tiny background skinny-dipper, a nude
  garden statue. If a figure is too small to be sure about, open the original. If you still cannot tell, reject.
- Modern, Expressionist and avant-garde work (an era gate blocks dates after 1915 and a surname blocklist covers the
  1900–1915 avant-garde; named Impressionists are allowed through).
- Anything frightening to a child: fires, storms with ships in peril, drowning, corpses, skulls, dead animals
  (game still lifes), animal cruelty.
- Not a safety issue but rejected for the wall: grayscale photographs of paintings, near-black canvases, photos
  that include the gilt frame, shaped or oval panels, museum colour-calibration strips that cannot be trimmed.

## Named lists (owner-chosen works)
`frame-curate named <list.json>` applies only the CORE block (nudity, gore, death, religious narrative, mythology,
modern). Military subjects are allowed because the owner picked the works. An entry may carry `"allow": true` to
skip the text gate entirely. Minimum aspect ratio 1.2 instead of 1.3. Everything still goes on a sheet and gets
looked at.

## How the gates work
- `SUBJECT_BLOCK_CORE` and `SUBJECT_BLOCK_STRICT`: lowercase substring matches on title + museum subject tags +
  description. Deliberately broad; a few word-bounded entries keep place names like Sainte-Adresse, Marseille,
  Toledo and Mount Holyoke from being blocked.
- `SAFE_TAGS`: in the default harvest a candidate must carry at least one landscape / still-life / garden type tag.
- `WATCH_TAGS`: figures, women, men, bathers, children. Not a block, a flag: those candidates are listed by
  `frame-curate status` and should be opened at full size.
- `_era_ok`: surname blocklist + date backstop (> 1915 rejected) with an Impressionist allowlist.
- `_landscape`: width ≥ 1.3 × height (1.2 for named lists). Portrait works look small on a 16:9 panel.

## Review practice that worked
- Contact sheets at 500 × 340 px per cell are enough to judge subject and mood; they are not enough to judge a
  figure. Open the original for anything with people.
- A second, independent pass helps. Two reviewers with different lenses (bodies/gore vs subject/theme) caught
  eleven pieces a careful first pass had let through: background skinny-dippers, bathing-machine beaches, a
  shirtless fisherman, nude garden statuary, soldiers in a harbour view.
- Keep the gallery smaller than the TV's limit. A trusted 100–170 is better than a maxed-out 227.
