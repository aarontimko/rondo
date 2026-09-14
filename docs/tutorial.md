# Tutorial: a jam, start to finish

This is the loop rondo is built for. You play, the model listens to what you
say about it, and every step is a small script call on the project that is
open in Reaper. Nothing before the last section needs a render; you press
play yourself.

## 1. Start from something you played

Arm a track for the virtual keyboard and play a bass idea over the click.

```bash
python scripts/add_instrument.py --track "Bass" --instrument surge --patch "Wide Bassline"
python scripts/record.py --track "Bass" --source keyboard --monitor on
```

Then ask for the timing to be worked out. The first transcription is usually
wrong, and that is fine: you correct the rhythm in words ("it's A A A G A B on
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
python scripts/install_samples.py            # once; fetches the CC0 one-shots
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
n=0
for patch in "Brassy" "OB-8 Jump" "Toto Brass" "Synth Brass 1"; do
  n=$((n + 1))
  python scripts/add_instrument.py --track "Brass alt $n" --instrument surge --patch "$patch"
  python scripts/write_notes.py --track "Brass alt $n" --bar 9 --notes @brass.json --replace
  python scripts/track.py mute --track "Brass alt $n"
done
```

The same trick works for phrases: four tracks that share bars 1-3 of a riff
and differ only in bar 4. If two candidates sound good playing at the same
time, ask for the blend as a fifth option.

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
(the riff moved up, then up again, with one bar that pulls back to the home
key), the full band twice with one new harmony line, outro. Name every
section as a region so you can talk about "the breakdown" from then on.

```bash
python scripts/write_notes.py --track Bass --bar 33 --bars 8 --replace --notes @bass_groove.json --transpose 3
python scripts/project.py region --name "Bridge" --from 33 --to 40
```

Then shape it: a volume envelope that dips and swells on each held chord, a
filter that closes over the outro. Say the shape in words ("normal, then
minus ten, then growing steadily to plus ten by the end of the bar"). The
model prints back what it drew, bar by bar, so check the numbers against
what you asked for.

## 5. Ask what it would polish

When the arrangement is right, ask "what would you polish?" and expect a
numbered list. Each item should say what it would change, why, and one real
alternative, so you can answer by number ("1 yes, 2 yes, 3 the alternative,
4 skip") without re-reading anything. The usual items:

* **Headroom**: the loudest moment should not hit the top of the meter, so
  the whole mix comes down a little rather than any one track clipping.
* **Stereo width**: which parts sit in the middle (kick, bass, lead) and
  which spread left and right (pads, hats), so they stop covering each
  other.
* **Reverb**: how much room each sound seems to be in; pads want more,
  drums and bass want almost none, or the low end turns to mud.
* **Cymbal placement**: a crash on the first bar of a section and nowhere
  else says "new section" more clearly than one every four bars.
* **Feel**: nudging hi-hats or ghost notes a fraction late loosens a groove
  that sounds mechanical; a little goes a long way.

## 6. Try several versions at once

Decisions you are unsure about do not need to be argued. Ask for versions. Each
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

## 7. Getting the volumes right

Once the notes are in, most of what is left is volume: one instrument is
too loud, another disappears in one section, a build-up does not feel like
a build-up. You fix all of it the same way as everything else here, by
saying what you hear. The [theme song](https://github.com/aarontimko/rondo/releases/download/v0.1.0/rondo-theme-song.mp3)
on the Releases page is the README's demo song after an hour of sentences
like "the pluck is too quiet after the bridge" and "make the second pluck a
bit louder for the last four bars". Here is what you can ask for and why.

**Turn a whole track up or down.** Every track has a fader, which sets how
loud that track is for the whole song. "Drums are a bit loud compared to the
pluck" or "cymbals 3 dB higher" moves the fader, and the model saves the
project after each move so nothing is lost.

**Raise or lower a track in one section only.** That is a volume envelope: a
line drawn over the timeline that adds to the fader in the bars you name.
"Lift the pluck 3 dB through the breakdown and the bridge" holds +3 over
those bars and returns to normal after. In Reaper's default mode the
envelope sits on top of the fader, so an envelope at 0 dB changes nothing,
and you can keep moving the fader afterwards without redrawing anything.

**Make a build-up or a fade.** Describe the shape in bars: "swell over bars
49 and 50, hold until the start of bar 53, then ease back by bar 57", or
"rise slowly to a couple of dB by the end of the eighth bar, then come back
down by the twelfth". Two things make these work. Say "start from the level
it already has" for a lift, otherwise the first bar dips before it rises.
And keep the numbers small: 2 to 4 dB over one or two bars is clearly
audible, while 10 dB spread over eight bars is not, because the ear hears
the shape of the change rather than the total. If a build still does not
land, ask for the notes to hit harder as well ("ramp the velocity up across
those bars"); on a pluck that changes the tone, which is more noticeable
than loudness alone.

**Give a sound its own track when you need to control it alone.** A drum
kit on one track has one fader for the whole kit, so "the clap is too loud
but the kick is right" cannot be fixed with it. Ask for "the clap on its own
track" and the model builds a second kit track holding only that sample,
moves the clap notes there, and gives it a fader. The same works for
cymbals, and for melodic parts: a second pluck track with the same patch,
playing the high answering notes, is what makes "the high pluck a bit
louder in the climax" a one-line request.

**Ask what is already drawn when reusing a project.** Deleting a track's
notes does not delete its envelope. If a part goes quiet where the notes say
it should not, ask "is there an old envelope on the pluck?"; a fade from an
earlier version of the song is the usual answer.

**Ask for small edits once you have adjusted things by hand.** "Remove the
cymbal at bars 61 and 69" deletes two notes and touches nothing else.
Rewriting a whole section would restore the same notes but is the wrong
habit next to envelope points you have moved yourself.

**Reorder tracks however you like.** Every script finds a track by its name,
so dragging tracks around in Reaper changes nothing for the model.

**Do it yourself when that is quicker.** If you already know your way around
a DAW, nothing stops you from dragging a fader or an envelope point while the
model works. If you do not, watch: after the model has added envelope points
and set their values on your behalf a few times, you will see where they sit
and what they do, and reaching in to nudge one yourself becomes the natural
next step. Tell the model when you have done that ("I moved some points by
hand, don't revert anything you find") and it will edit around your work.

**Stop playback before rendering.** A synth that is still sounding when the
render starts leaves its tail in the first bar of the file. Ask for the
render to a folder git ignores, such as `render/`; audio never goes into the
repository.
