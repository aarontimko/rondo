# Tutorial: a jam, start to finish

This is the loop rondo is built for. You play, the model listens to what you
say about it, and every step is a small script call on the project that is
open in Reaper. Nothing here needs a render; you press play yourself.

## 1. Start from something you played

Arm a track for the virtual keyboard and play a bass idea over the click.

```bash
python scripts/record.py --track "Bass" --source keyboard --monitor on
```

Then ask for the timing to be worked out. The first transcription is usually
wrong, and that is fine: you rule on the rhythm in words ("it's A A A G A B on
the 1, 2, 3-and, 4-and, and separate notes on the walk down") and the model
writes it back cleanly as a grid file, replacing the raw take.

```bash
python scripts/write_notes.py --track Bass --bar 1 --grid bass.txt --replace
```

Keep the grid file. It is the source of truth for the part, and everything
later is generated from it.

## 2. Build the groove around it

One pass, all at once, then iterate: drums from a kit, velocities and octave
jumps on the bass, a second instrument answering it, a filter envelope that
breathes every four bars, and a 16-bar form with two named regions.

```bash
python scripts/build_kit.py --track Drums
python scripts/write_notes.py --track Drums --bar 1 --notes @drums.json --replace
python scripts/add_instrument.py --track Stabs --instrument surge --patch "Clavi"
python scripts/automate.py fx-param --track Bass --fx 0 --param "A Filter 1 Cutoff" \
    --from 1 --to 3 --start 0.30 --end 0.62 --shape fast-end
python scripts/copy_section.py --from 1 --to 8 --at 9 --region "Groove B"
```

Ask for it all, listen, then say what is wrong. "Sounds good so far, get rid
of the raw take" is a complete instruction.

## 3. Audition instead of deciding

When you want to hear options, do not pick one. Ask for every candidate as
its own muted track, identical except for the one thing being compared, and
solo them in Reaper while the song loops.

```bash
for n in 1 2 3 4; do
  python scripts/add_instrument.py --track "Brass alt $n" --instrument surge --patch "$PATCH_$n"
  python scripts/write_notes.py --track "Brass alt $n" --bar 9 --notes @brass.json --replace
  python scripts/track.py mute --track "Brass alt $n"
done
```

The same trick works for phrases: four tracks that share bars 1-3 of a riff
and differ only in bar 4. Playing two of them together by accident is how the
best ending in one session was found; ask for the blend as a fifth option.

When one wins, promote it and delete the rest:

```bash
python scripts/track.py rename --track "Brass alt 5" --to "Brass (Surge: Brassy)"
python scripts/track.py delete --track "Brass alt 1" --yes
```

## 4. Stretch it into a song

A 16-bar groove becomes a song by taking things away and adding them back.
Every section except the bridge is the same material with parts muted or
thinned, so the model can build the whole form in one pass from the note
files: intro, groove, groove with the hook, breakdown, a bridge in a new key
(the riff transposed up, then up again, with a turnaround bar on the dominant
chord), the full band twice with one new harmony line, outro. Name every
section as a region so you can talk about "the breakdown" from then on.

```bash
python scripts/write_notes.py --track Bass --bar 33 --bars 8 --replace --notes @bass_groove.json --transpose 3
python scripts/project.py region --name "Bridge" --from 33 --to 40
```

Then shape it: a volume envelope that dips and swells on each held chord, a
filter that closes over the outro. Say the shape in words ("normal, then
minus ten, then growing steadily to plus ten by the end of the bar") and
check the read-back the model prints.

## 5. The polish list

When the arrangement is right, ask for the polish list: headroom, stereo
width, reverb, cymbal placement, feel. The model presents each as a numbered
item with a recommendation and a real alternative, and you rule by number
("1 rec, 2 rec, 3 alt, 4 skip").

## 6. Try several versions at once

Rulings you are unsure about do not need to be argued. Ask for versions. Each
one is a project clone with a letter, built in a chain: save the open project
under a new name (the tab adopts the name, nothing reloads), apply that
version's changes, save, and repeat.

```bash
python scripts/project.py save --as ~/songs/take-A.rpp --project take
# ... apply version A's changes ...
python scripts/project.py save
python scripts/project.py save --as ~/songs/take-B.rpp --project take-A
# ... apply version B's changes ...
python scripts/project.py save
```

Two things make this cheap. Every part is regenerated from a note file, so a
change like "crash only on section starts" is a different drum file written
over the same bars, not an edit to undo. And the changes are ordered so that
each version is derived from the previous one by adding, never by removing.
Open the versions in Reaper yourself afterwards and keep the one you like;
the original file is never touched.
