# Plugging a game in

> **Not affiliated.** fpsdet is not affiliated with, endorsed by, or sponsored by Battlestate Games (Escape from Tarkov), Activision (Call of Duty), Electronic Arts or DICE (Battlefield), or the developers of WARDOGS. Those names are trademarks of their owners. They are used here only to describe which fields a server for that kind of game could emit. Only those studios could integrate fpsdet into their games. Nothing on this page comes from them. Where it describes how a game works, it is an assumption from public play and public guides, not knowledge of their servers.

No profile in this repo is a studio export. Do not ban from a copied community table. A server that simulates the player has already applied a speed cap and a recoil for the current stance, perks, and attachments. Send those numbers as `expected_max_ground_speed_mps` and `expected_min_recoil_pitch_deg`. The curves in `profiles/` are the fallback for when a sample has no expected value, and they are how you unit-test the idea.

Set `"aim_group": "weapon_id"` when guns inside one class are not the same fight. A bolt gun and a semi-auto sniper should not share an accuracy ceiling.

## Fields a server for these games could fill

| Field | Why it exists |
| --- | --- |
| `weapon_id`, `mod_set` | Recoil and aim baselines per build. Unknown builds stay untrained. |
| `loadout_weight_kg` | The kit they are actually wearing, after the server's own weight math. |
| `expected_max_ground_speed_mps` | The cap for this stance, this weight class, this perk, this second. |
| `speed_mps`, `on_ground` | What the body did, and whether it was a sprint rather than a jump. |
| `displacement_cause` | Blast, vehicle, parachute, ability, ragdoll. The false-positive valve. |
| `recoil_pitch_deg`, `expected_min_recoil_pitch_deg`, `spray_index` | No-recoil scripts, with mods allowed to lower the floor. |
| `hit`, `hitbox`, `distance_m` | Human aim baseline. Server hit result only. |
| `through_geometry` | Info abuse that turns into a shot through a wall the server can prove. |
| `applied_recoil_pitch_deg`, `compensation_pitch_deg` | The kick the server applied, and the player command. Same-tick cancel, then the leftover after that kick is removed. |
| `information_state`, `aim_jitter_deg`, `enemy_id` | Whether this client could have known, how noisy the aim was, and which enemy. A bad `unknowable` label manufactures cases. |
| `hidden_track_ms` | Time on a mover the server had not made visible. |
| `private_track_ms` | Time on a replay of someone else's movement, placed where this client could not see or hear it. No decoy bit. |
| `challenge_id`, `challenge_track_ms` | The same, as a planned challenge: which one, and the time on it. See [challenges.md](challenges.md). |
| `wire_error_deg`, `picture_error_deg`, `interp_delay_ms` | Error to the snapshot just sent, error to the picture the official client draws, and the interpolation delay. Omit any one you cannot measure. |
| `skill_band` | The queue they entered, so a pub-stomp is a watch and not a fake elite. |

Extra numbers are a row in `extra_metrics` plus the field on the event. ADS time, stamina recovery, sway, ergonomics, and time-to-kill all fit that slot. Direction `low` or `high`, kind `primary` or `supporting`, and the group (`weapon_id`, `weight_class`, `skill_band`, `build_key`).

## WARDOGS

Public gear guides from September 2026 describe weight as a movement rule in WARDOGS. They list five equipment classes with about these move penalties: lightest 0–10 kg with no penalty, light 10–17 kg about −5%, medium 17–27 kg about −13%, heavy 27–40 kg and super-heavy about −20%. Community tables put body armor around 3 kg at level 1 and 18 kg at level 4, so armor dominates the total. Those penalties are in [profiles/wardogs.json](../profiles/wardogs.json). Absolute meters per second are not, on purpose. The guides do not publish a server sprint speed, and a wrong constant would flag legal players.

The same guides describe an adrenaline pen that lowers the equipment weight class. If the server works that way and you score against the raw kit weight, a legal pen looks like a speedhack. Send `expected_max_ground_speed_mps` after the pen, the stance, and the class math. That one field covers the pen without a special case.

Tag anything the game has that moves a body: parachute, explosion, ragdoll, vehicle, ladder. A player blown across the ground is `explosion` or `knockback` for the whole throw, not one sample. The two-frame glitch filter is the backstop for a tag you missed. It is not a substitute for the tag.

`profiles/wardogs.json` sets `aim_group` to `weapon_id`. If attachments change recoil, either send the expected recoil floor from the server's item assembly or add a `recoil_floors` row per build you care about. Builds you have not measured stay untrained.

The synthetic demo's `weight-cheat` player is this case: about 10 kg of kit, sprinting at the cap of the lightest class, on the ground, cause `none`, for a full run of samples. `blasted` is the same speed with the cause set, and it stays clean. `adrenaline` is the pen case: the curve would flag the weight, the server expected-cap does not.

## Escape from Tarkov

From public play, Tarkov has the same shape as WARDOGS, only harsher. Weight and inertia appear to change acceleration and top speed. The weapon, attachments, ammo, and ergonomics appear to change recoil and ADS. Armor and a backpack add weight. A server that applies those effects should send its post-effect speed cap and its recoil floor for the assembled gun. A profile of guessed ergo numbers will false-flag.

Innocent causes that matter: grenade and ragdoll, stimulus or pain effects if they launch the body, and any admin or disconnect correction. `through_geometry` is only as good as the server trace.

If part of the felt recoil is presented only on the client, such as a camera shake, score the view delta the server replicated during the spray, compared with the kick the server thinks that build produces. A client-only camera shake is not evidence, because a cheat will not play it.

## Call of Duty

If the build is the weapon plus an attachment list (Gunsmith), the build key is that list, not the weapon class. If recoil differs by barrel, muzzle, underbarrel, and optic, send the expected floor the server computed for that build.

If movement caps change by stance, tac-sprint, slide, dive, swim, and perk or specialist speed stay legal when `expected_max_ground_speed_mps` is the cap of the stance they are in. A profile that only knows walk speed will call tac-sprint a cheat. Score ground sprints with `on_ground`. Leave slide-cancel quirks to the server cap rather than a hand-written exception.

Modes with vehicles, redeploys, and explosions (Warzone, Ground War) need those tagged as innocence causes. Killstreaks and scorestreaks that move the player or fire for them should be their own `weapon_id` or an `ability` cause, so a VTOL does not enter an infantry accuracy cohort.

Use the matchmaking bracket of that playlist as the rank band. If ranked and casual play use different rules, keep them in separate ceilings. A profile that mixes them into one ceiling will flag one of them.

## Battlefield

Vehicles are likely the biggest source of false positives. A player inside a vehicle, on a wing, under a parachute, or being flung by destruction is not an infantry sprint. Tag `vehicle`, `parachute`, `explosion`, or `knockback` for the whole interval, including the exit if the exit launches them.

If infantry recoil is per weapon and per attachment, key it by build, as for Gunsmith above. Tag traversal gadgets (a grapple, a wingsuit, a launcher) as `ability` or `launch`. Do not put vehicle-seat accuracy into the infantry rifle cohort: send a distinct `weapon_class` for vehicle weapons so the human ceiling is other vehicle gunners.

On very large maps, a single distance baseline means little. `map_id` is on the event for your own splits. The reference cohort keys distance by weapon and rank, not by map. When one weapon is used on a 100 m infantry map and a 400 m vehicle map, split `weapon_class` or the distance flag will lie.

## What "training" means here

Training is the cohort file `fpsdet baseline` writes. It is the distribution of humans on each band, weapon, and build, from the lake you dumped. A new gun, a new mod combination, or a new weight class with no curve produces `insufficient` / untrained until `min_cohort_players` humans have enough samples. That is intentional. Flagging a build you have never measured is how legal players get removed.

The optional AI call does not do this training. It explains a case after the numbers exist. A later supervised model, trained on your reviewers' labels, reads `features.csv`. It does not replace the gear rules. A sustained break of the server's own speed cap does not need a neural net.
