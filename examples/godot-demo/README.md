# Godot demo: enemy hit feedback

A tiny Godot 4.3 project with an enemy you can click (or hit with Space). It
has a `take_damage()` and a `die()` event and **no sound** — the starting point
for the acceptance scenario.

Ask Hermes:

> In examples/godot-demo, finish the enemy hit and death feedback with sound.
> Keep the change minimal and show me the diff first.

Expected flow: `inspect_game_audio` finds `take_damage` and `die` as silent
events → `prepare_game_audio` generates `audio/sfx/enemy_hit.wav` and
`enemy_death.wav` → `integrate_game_audio` previews, applies and validates.

Automated trigger check (headless, no speakers needed):

```bash
godot --headless --path examples/godot-demo --import
godot --headless --path examples/godot-demo --script res://tests/trigger_test.gd
```

It hits the enemy until it dies and checks that every hit starts an
`AudioStreamPlayer`. It prints `RESULT overall=FAIL` on the untouched demo and
`RESULT overall=PASS` after integration. It proves the events trigger
playback, not how the sounds sound — listen in the editor (F5) for that.

Two more headless checks ship with the demo:

- `res://tests/music_test.gd`: after integrating music with `mode: "music"` on
  `main.gd`'s `_ready`, it checks that every WAV in `res://audio/music` imports
  as a loop and that the `HLGA_Music` player is playing and looping.
- `res://tests/ready_test.gd`: checks that a sound fired from a `_ready()`
  during scene setup still plays.

Generated `audio/` and Godot's `.godot/` cache are git-ignored. Work on a copy
if you want to keep the demo pristine; every integration can also be reverted.
