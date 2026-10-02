# Plugging a game in

No profile in this repo is a studio export. Do not ban from a copied community table. The server that simulates the player already applied a speed cap and a recoil for the current stance, perks, and attachments. Send those numbers as `expected_max_ground_speed_mps` and `expected_min_recoil_pitch_deg`. The curves in `profiles/` are the fallback for when a sample has no expected value, and they are how you unit-test the idea.

Set `"aim_group": "weapon_id"` when guns inside one class are not the same fight. A bolt gun and a semi-auto sniper should not share an accuracy ceiling.

## Fields every one of these games can fill

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
| `wire_error_deg`, `picture_error_deg`, `interp_delay_ms` | Error to the snapshot just sent, error to the picture the official client draws, and the interpolation delay. Omit any one you cannot measure. |
| `skill_band` | The queue they entered, so a pub-stomp is a watch and not a fake elite. |

Extra numbers are a row in `extra_metrics` plus the field on the event. ADS time, stamina recovery, sway, ergonomics, and time-to-kill all fit that slot. Direction `low` or `high`, kind `primary` or `supporting`, and the group (`weapon_id`, `weight_class`, `skill_band`, `build_key`).

## WARDOGS

Weight is a real rule in WARDOGS. Public gear guides in September 2026 describe five equipment classes and about these move penalties: lightest 0–10 kg with no penalty, light 10–17 kg about −5%, medium 17–27 kg about −13%, heavy 27–40 kg and super-heavy about −20%. Armor dominates the total: community tables put body armor around 3 kg at level 1 and 18 kg at level 4. Those penalties are in [profiles/wardogs.json](../profiles/wardogs.json). Absolute meters per second are not, on purpose. The guides do not publish a server sprint speed, and a wrong constant would flag legal players.

The adrenaline pen reduces equipment weight class. If you score against the raw kit weight, a legal pen looks like a speedhack. Send `expected_max_ground_speed_mps` after the pen, the stance, and the class math. That one field covers the pen without a special case.

Also tag parachute, explosion, ragdoll, vehicle, and ladder. A player blown across the ground is `explosion` or `knockback` for the whole throw, not one sample. The two-frame glitch filter is the backstop for a tag you missed. It is not a substitute for the tag.

`aim_group` is `weapon_id`. Attachments change recoil, so either send the expected recoil floor from the server's item assembly or add a `recoil_floors` row per build you care about. Builds you have not measured stay untrained.

The synthetic demo's `weight-cheat` player is the shape you described: about 10 kg of kit, sprinting at the cap of the lightest class, on the ground, cause `none`, for a full run of samples. `blasted` is the same speed with the cause set, and it stays clean. `adrenaline` is the pen case: the curve would flag the weight, the server expected-cap does not.

## Escape from Tarkov

The same shape as WARDOGS, with a harsher version of the same facts. Weight and inertia change acceleration and top speed. Weapon, attachment, ammo, and ergonomics change recoil and ADS. Armor and a backpack change weight. Send the server's post-effect speed cap and the server's recoil floor for the assembled gun. A profile of guessed ergo numbers will false-flag.

Innocent causes that matter: grenade and ragdoll, stimulus or pain effects if they launch the body, and any admin or disconnect correction. `through_geometry` is only as good as the server trace.

Much of the felt recoil in Tarkov is presented on the client. Score the view delta the server replicated during the spray, compared with the kick the server thinks that build produces. A client-only camera shake is not evidence, because a cheat will not play it.

## Call of Duty

Gunsmith means the build key is the weapon plus the attachment list, not the weapon class. Recoil floors differ by barrel, muzzle, underbarrel, and optic. Send the expected floor from the server's gunsmith result.

Movement caps change by stance. Tac-sprint, slide, dive, swim, and perk or specialist speed are legal when `expected_max_ground_speed_mps` is the cap of the stance they are in. A profile that only knows walk speed will call tac-sprint a cheat. Score ground sprints with `on_ground`. Leave slide-cancel quirks to the server cap rather than a hand-written exception.

Warzone and Ground War add vehicles, redeploys, and explosions. Those are innocence causes. Killstreaks and scorestreaks that move the player or fire for them should be their own `weapon_id` or an `ability` cause, so a VTOL does not enter an infantry accuracy cohort.

Rank band is the matchmaking bracket of that playlist. A profile that mixes casual and ranked CDL rules into one ceiling will flag one of them.

## Battlefield

Vehicles are the dominant false positive. A player inside a vehicle, on a wing, under a parachute, or being flung by destruction is not an infantry sprint. Tag `vehicle`, `parachute`, `explosion`, or `knockback` for the whole interval, including the exit if the exit launches them.

Infantry recoil is per weapon and per attachment, same as gunsmith. Gadgets (grapple, wingsuit, launchers, traversal) are `ability` or `launch`. Do not put vehicle-seat accuracy into the infantry rifle cohort: send a distinct `weapon_class` for vehicle weapons so the human ceiling is other vehicle gunners.

Conquest-scale maps make a single distance baseline meaningless. `map_id` is on the event for your own splits. The reference cohort keys distance by weapon and rank, not by map. When one weapon is used on a 100 m infantry map and a 400 m vehicle map, split `weapon_class` or the distance flag will lie.

## What "training" means here

Training is the cohort file `fpsdet baseline` writes. It is the distribution of humans on each band, weapon, and build, from the lake you dumped. A new gun, a new mod combination, or a new weight class with no curve produces `insufficient` / untrained until `min_cohort_players` humans have enough samples. That is intentional. Flagging a build you have never measured is how legal players get removed.

The optional AI call does not do this training. It explains a case after the numbers exist. A later supervised model, trained on your reviewers' labels, reads `features.csv`. It does not replace the gear rules. A sustained break of the server's own speed cap does not need a neural net.
