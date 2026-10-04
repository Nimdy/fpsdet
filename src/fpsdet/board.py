"""One offline review desk. This is the demo a person can see."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .models import GameProfile, curve_speed
from .ops import week_payload
from .opsview import OPS_CSS, OPS_HTML, OPS_JS, payload_json, render_dashboard
from .signals import WIRE_ERROR_RATIO, WIRE_MIN_GAP_DEG, command_residual, pearson
from .statsutil import median
from .synthetic import ROOT, Demo, replay_scene
from .week import build_week

# Tour order. The queue is a story, not an alphabetical dump.
TOUR = [
    "weight-cheat",
    "blasted",
    "glitch",
    "adrenaline",
    "no-recoil",
    "modded-recoil",
    "new-gun",
    "mirror-script",
    "late-compensate",
    "fire-rate",
    "metronome",
    "wall-eye",
    "angle-holder",
    "rage",
    "rank-outlier",
    "account-changed",
    "reported-streamer",
    "legal-heavy",
    "elite-human",
    "weak-human",
    "small-sample",
    "quiet-radar",
    "steady-hands",
    "listened",
    "clone-source",
    "clone-buyer",
    "radar-friend",
    "callout-friend",
    "replay-lock",
    "real-fight",
    "wire-lock",
    "picture-track",
]

COPY = {
    "weight-cheat": {
        "group": "Open these",
        "chart": "speed",
        "title": "10 kg on the ground, running at the 3 kg cap",
        "lede": "Thirty samples. Cause none. The server cap for a 10 kg kit is 5.6 m/s. The lightest kit, about 3 kg, is allowed 7.2. This player held 7.1. That is the finding. No model and no rank. The rule is the gear.",
        "control": "blasted",
        "control_label": "Same speed, tagged as a blast",
    },
    "blasted": {
        "group": "Same numbers, not a cheat",
        "chart": "speed",
        "title": "Same 7.1 m/s. The server called it an explosion.",
        "lede": "Identical weight, identical speed. Every sample is displacement_cause explosion, so the run is excluded. A blast that throws a player is not a speedhack. Forget the tag and a two-frame spike still does not fire. A long untagged throw will. Tag the cause.",
        "control": "weight-cheat",
        "control_label": "The untagged version of this sprint",
    },
    "glitch": {
        "group": "Same numbers, not a cheat",
        "chart": "speed",
        "title": "Two frames at 15 m/s, then a normal sprint",
        "lede": "The longest illegal run is 2 samples. The bar is 25. The case notes a possible glitch and the decision stays clean. One bad tick is not a player.",
    },
    "adrenaline": {
        "group": "Same numbers, not a cheat",
        "chart": "speed",
        "title": "The weight table says illegal. The server cap says legal.",
        "lede": "10 kg at 7.0 m/s fails the printed curve. Each sample carries expected_max_ground_speed_mps 7.2, the cap after an adrenaline pen, a perk, or a tac-sprint. The server number wins. A stale spreadsheet does not get to ban it.",
    },
    "no-recoil": {
        "group": "Open these",
        "chart": "recoil",
        "title": "Stock rifle. The kick never arrives.",
        "lede": "No mods. The floor for this AK is 1.6°. From shot 3 on, pitch sits at 0.05°, under a quarter of that floor, for the rest of the magazine. That is a review by itself.",
        "control": "modded-recoil",
        "control_label": "Same gun with the mods attached",
    },
    "modded-recoil": {
        "group": "Same numbers, not a cheat",
        "chart": "recoil",
        "title": "Same gun, compensator and grip, 0.8° of kick",
        "lede": "This build's floor is 0.7°. Measured kick is 0.8°. Legal. One global \"no recoil\" number would have banned a player for equipping the attachment.",
        "control": "no-recoil",
        "control_label": "The stock gun with the script",
    },
    "new-gun": {
        "group": "Same numbers, not a cheat",
        "chart": "recoil",
        "title": "0.02° of kick, and it is still not a case",
        "lede": "brand-new has no curve and not enough humans on that build. Untrained guns are listed and left alone. Flagging a weapon you have never measured is how a patch day becomes a ban wave.",
    },
    "fire-rate": {
        "group": "Open these",
        "chart": "gaps",
        "title": "Every gap is under the rifle's own cycle",
        "lede": "This rifle may fire every 90 ms. One tick of slack makes the line 75 ms. Every gap in this window sits under that line. The aim is ordinary. The macro is the finding.",
    },
    "mirror-script": {
        "group": "Open these",
        "chart": "mirror",
        "title": "The camera still kicks. The command is that kick, backwards, same tick.",
        "lede": "Net pitch stays near 1.35°, above a quarter of the 1.6° floor, so the no-recoil rule is quiet. The player command correlates with the server kick at about −1.00 on the same tick and near zero one shot later. A person pulls late.",
        "control": "late-compensate",
        "control_label": "The same kicks, pulled one shot late",
    },
    "late-compensate": {
        "group": "Same numbers, not a cheat",
        "chart": "mirror",
        "title": "The command follows the previous kick.",
        "lede": "Same kick sequence. The pull-down is about half of the last shot, plus a little noise, one shot late. Same-tick correlation stays near zero. That is a person. The white line is the kick plus that late command. It never holds a run under a quarter of the floor, so the no-recoil rule stays quiet.",
        "control": "mirror-script",
        "control_label": "The same kicks, cancelled on the tick",
    },
    "metronome": {
        "group": "Open these",
        "chart": "gaps",
        "title": "Legal fire rate. No variation.",
        "lede": "Every gap is 140 ms. The legal line is 75. The sample standard deviation is 0.00 ms across 59 gaps. A person does not hold a cycle to the millisecond for that long. A server-paced full-auto sets server_paced and is left alone. Stamps that all land on tick_ms are left alone.",
        "control": "fire-rate",
        "control_label": "The one that is simply too fast",
    },
    "wall-eye": {
        "group": "Open these",
        "chart": "hidden",
        "title": "The aim was on a player the server still had hidden.",
        "lede": "This window is too short to score aim. Across 20 shots the view stayed on a moving enemy for 1600 ms while that enemy was not visible. Corner pre-aim is a different field. This one requires the server to know the enemy was hidden.",
        "control": "angle-holder",
        "control_label": "A corner that was only pre-aimed",
    },
    "angle-holder": {
        "group": "Same numbers, not a cheat",
        "chart": "hidden",
        "title": "Fast onto the angle. Zero time on a hidden mover.",
        "lede": "Acquire is quick because the angle was already held. hidden_track_ms is 0. Holding a corner is not a wallhack. A visibility query that marks a visible enemy as hidden would manufacture the other card. That is an emitter bug.",
        "control": "wall-eye",
        "control_label": "Time spent on someone still hidden",
    },
    "rage": {
        "group": "Open these",
        "chart": "aim",
        "title": "Queued average. Past every elite in the baseline.",
        "lede": "The lower bound on accuracy is past the best elite we have actually measured, and the headshot bound clears the same kind of bar. Two rates past the best measured human is a review. A good player is not this.",
        "control": "rank-outlier",
        "control_label": "Someone who is only better than their rank",
    },
    "rank-outlier": {
        "group": "Human baseline",
        "chart": "aim",
        "title": "Better than their rank. Inside the best humans.",
        "lede": "Above the average band. Under the best elite. That is a smurf or a strong player in a low bracket. Watch them. This is the case an auto-ban would get wrong.",
        "control": "rage",
        "control_label": "The one who clears the human ceiling",
    },
    "account-changed": {
        "group": "Human baseline",
        "chart": "aim",
        "title": "This account stopped shooting like itself",
        "lede": "Same rank, same rifle. This window is confidently above the account's own history and still inside the humans. A bought cheat on an old account often looks like this before it looks superhuman. Watch.",
    },
    "reported-streamer": {
        "group": "Reports are a queue",
        "chart": "aim",
        "title": "25 reports. Ordinary aim.",
        "lede": "Reports moved this player to the front of the scan. The rifle sits on the average of the average band. A brigade can make you look today. It cannot convict anyone.",
        "control": "rage",
        "control_label": "What a real review looks like",
    },
    "legal-heavy": {
        "group": "Same numbers, not a cheat",
        "chart": "speed",
        "title": "18 kg, 5.0 m/s, under a 5.4 cap",
        "lede": "Heavy kit, legal sprint, ordinary rifle. This is the population the baseline is made of.",
    },
    "elite-human": {
        "group": "Human baseline",
        "chart": "aim",
        "title": "The best band, shooting like the best band",
        "lede": "Sitting on the elite median does not open a case. The ceiling is built from these players. They are why the rage account is obvious and why this one is not.",
    },
    "weak-human": {
        "group": "Human baseline",
        "chart": "aim",
        "title": "Worse than average. That is not suspicious.",
        "lede": "Low accuracy does not move the score. The only direction that matters is the one a hack moves: too accurate, too fast for the kit, too little recoil for the build.",
    },
    "small-sample": {
        "group": "Reports are a queue",
        "chart": "aim",
        "title": "10 for 10, and the desk refuses it",
        "lede": "A perfect short round is not a case. Aim waits for 40 shots because the bound on 10 shots is too wide to call anyone superhuman.",
    },
    "quiet-radar": {
        "group": "Open these",
        "chart": "jitter",
        "title": "The aim gets quiet only while this client could not have known.",
        "lede": "Knowable moments sit at 0.90° of aim noise. The same player drops to 0.08° on the samples the server marked unknowable: no line of sight and no audio. A player who is smooth all the time does not do this. A missing label is ignored.",
        "control": "listened",
        "control_label": "The same drop, while they could hear",
    },
    "steady-hands": {
        "group": "Same numbers, not a cheat",
        "chart": "jitter",
        "title": "Smooth while looking. Smooth while unknowing. No drop.",
        "lede": "Both halves sit at 0.40°. The rule needs the noise to collapse only in the unknowable window. A steady player stays clean.",
        "control": "quiet-radar",
        "control_label": "The one who only gets quiet when they should not know",
    },
    "listened": {
        "group": "Same numbers, not a cheat",
        "chart": "jitter",
        "title": "Quiet, because the server says they could hear it.",
        "lede": "Aim noise falls to 0.08° on the second half. information_state is audio, so those samples are the knowable baseline, not a radar. Footsteps and a legal callout have to be labeled, or this card becomes the other one.",
        "control": "quiet-radar",
        "control_label": "The same numbers marked unknowable",
    },
    "clone-source": {
        "group": "Open these",
        "chart": "residual",
        "title": "Already a mirror review. The leftover is the product.",
        "lede": "The camera is written back to 1.35° so this is not another no-recoil card. The command still cancels the kick on the same tick. After that kick is removed, the leftover series is what the buyer is carrying.",
        "control": "clone-buyer",
        "control_label": "The customer who did not cancel the kick",
    },
    "clone-buyer": {
        "group": "Same leftover",
        "chart": "residual",
        "title": "Not a mirror. Same leftover as someone who is.",
        "lede": "Same-tick correlation with the kick stays near zero, so the mirror rule is quiet. The command after the kick and its lag are removed matches clone-source. That puts this account next in the scan. It does not convict them.",
        "control": "clone-source",
        "control_label": "The confirmed customer",
    },
    "radar-friend": {
        "group": "Open these",
        "chart": "lags",
        "title": "Eight swings, 40 ms after a teammate who could see through the wall.",
        "lede": "wall-eye is already a hidden-track review. This teammate swings the same enemy 40 ms later, while that enemy is still hidden from them. A voice needs longer than 350 ms. This is a watch. A person still has to decide it was a shared screen.",
        "control": "callout-friend",
        "control_label": "The teammate who waited long enough to have been told",
    },
    "callout-friend": {
        "group": "Same numbers, not a cheat",
        "chart": "lags",
        "title": "Same enemy. Late enough that somebody could have said it.",
        "lede": "The swings land 800 ms or more after wall-eye's last hidden sample. That is a callout. The party link is on the card. The decision stays clean.",
        "control": "radar-friend",
        "control_label": "The swings that beat a voice",
    },
    "replay-lock": {
        "group": "Open these",
        "chart": "private",
        "title": "Ordinary fight. The crosshair is on a body this client was not sent.",
        "lede": "Speed, recoil, and aim stay inside the humans. The gold path is a live route turned onto another heading, delayed, where this client had no sight and no audio. The red marks sit on it. There is no invisible flag. A one-second stay is the review. The public charts are allowed to look boring. The scorer reads the milliseconds, not this picture.",
        "control": "real-fight",
        "control_label": "The same window, on the enemy they could see",
    },
    "real-fight": {
        "group": "Same numbers, not a cheat",
        "chart": "private",
        "title": "They fought the body the server let them see.",
        "lede": "A few red marks clip the gold path. That is a spray crossing a volume, not a stay. The green marks stay on the enemy this client could see. One crossing stays clean. A server that marks a visible body as private would manufacture the other card.",
        "control": "replay-lock",
        "control_label": "The crosshair that stayed on the replay",
    },
    "wire-lock": {
        "group": "Open these",
        "chart": "wire",
        "title": "Same hits. The crosshair is on the snapshot, not the picture.",
        "lede": "Both players fight an enemy the server let them see. This crosshair sits on the quantized snapshot the tick it arrives. The picture the official client draws is one interpolation delay behind that. The hits match. Standing still would make the two positions the same, and nothing would fire.",
        "control": "picture-track",
        "control_label": "The same fight, on the picture the client draws",
    },
    "picture-track": {
        "group": "Same numbers, not a cheat",
        "chart": "wire",
        "title": "Same enemy. Same hits. The crosshair is on the picture.",
        "lede": "This is where a person aims. The official client draws the player where they were one delay ago. The snapshot is ahead of that. A legal aim can sit as close to the picture as the other card sits to the wire. A server that scores the wrong timeline manufactures the other card.",
        "control": "wire-lock",
        "control_label": "The crosshair that locked the snapshot",
    },
}


def _cap(event, profile: GameProfile) -> float | None:
    if event.expected_max_ground_speed_mps is not None:
        return event.expected_max_ground_speed_mps
    if event.loadout_weight_kg is None:
        return None
    return curve_speed(profile, event.loadout_weight_kg)


def _series(demo: Demo) -> dict[str, dict]:
    by_player: dict[str, dict] = {}
    profile = demo.profile
    for event in demo.events:
        slot = by_player.setdefault(
            event.player_id,
            {
                "speed": [],
                "recoil": [],
                "shots": [],
                "gaps": [],
                "mirror": [],
                "hidden": [],
                "private": [],
                "jitter": [],
                "wire": [],
            },
        )
        if event.event_type == "movement" and event.speed_mps is not None:
            cap = _cap(event, profile)
            limit = None if cap is None else cap * (1 + profile.speed_over_fraction)
            slot["speed"].append(
                {
                    "t": event.t_ms,
                    "speed": round(event.speed_mps, 3),
                    "cap": None if cap is None else round(cap, 3),
                    "cause": event.displacement_cause,
                    "weight": event.loadout_weight_kg,
                    "over": bool(
                        cap is not None
                        and event.displacement_cause == "none"
                        and event.on_ground is True
                        and event.speed_mps > (limit or 0)
                    ),
                }
            )
        if event.event_type == "shot":
            slot["shots"].append(event.t_ms)
            if event.recoil_pitch_deg is not None:
                floor = event.expected_min_recoil_pitch_deg
                if floor is None:
                    row = profile.recoil_floors.get(event.build)
                    floor = None if row is None else row.min_pitch_deg
                slot["recoil"].append(
                    {
                        "i": event.spray_index if event.spray_index is not None else len(slot["recoil"]),
                        "pitch": event.recoil_pitch_deg,
                        "floor": floor,
                    }
                )
            if (
                event.applied_recoil_pitch_deg is not None
                and event.compensation_pitch_deg is not None
            ):
                slot["mirror"].append(
                    {
                        "applied": round(event.applied_recoil_pitch_deg, 4),
                        "command": round(event.compensation_pitch_deg, 4),
                        "net": round(
                            event.applied_recoil_pitch_deg + event.compensation_pitch_deg, 4
                        ),
                    }
                )
            if event.hidden_track_ms is not None:
                slot["hidden"].append(event.hidden_track_ms)
            if event.private_track_ms is not None:
                slot["private"].append(event.private_track_ms)
            if (
                event.wire_error_deg is not None
                and event.picture_error_deg is not None
                and event.interp_delay_ms is not None
            ):
                slot["wire"].append(
                    {
                        "wire": round(event.wire_error_deg, 3),
                        "picture": round(event.picture_error_deg, 3),
                        "delay": round(event.interp_delay_ms, 3),
                    }
                )
            if event.aim_jitter_deg is not None and event.information_state:
                slot["jitter"].append(
                    {"jitter": round(event.aim_jitter_deg, 3), "state": event.information_state}
                )
    for slot in by_player.values():
        times = sorted(slot["shots"])
        slot["gaps"] = [times[i + 1] - times[i] for i in range(len(times) - 1)]
        slot["speed"].sort(key=lambda row: row["t"])
        slot["recoil"] = slot["recoil"][:48]
        slot["mirror"] = slot["mirror"][:48]
        del slot["shots"]
    return by_player


def _mirror_r(pairs: list[dict]) -> tuple[float | None, float | None]:
    if len(pairs) < 3:
        return None, None
    applied = [row["applied"] for row in pairs]
    command = [row["command"] for row in pairs]
    lagged = pearson(applied[:-1], command[1:]) if len(applied) > 4 else None
    return pearson(applied, command), lagged


def _aim_block(case, demo: Demo) -> dict:
    metric = {row.name: row for row in case.metrics}
    accuracy = metric.get("accuracy")
    headshot = metric.get("headshot_rate")
    return {
        "accuracy": None if accuracy is None else accuracy.player_value,
        "accuracy_bound": None if accuracy is None else accuracy.bound,
        "headshot": None if headshot is None else headshot.player_value,
        "headshot_bound": None if headshot is None else headshot.bound,
        "rank_p95": None if accuracy is None else accuracy.own_p95,
        "elite_accuracy_max": None if accuracy is None else accuracy.ceiling_extreme,
        "headshot_rank_p95": None if headshot is None else headshot.own_p95,
        "elite_headshot_max": (
            demo.anchors.get("elite_headshot_max")
            if headshot is None or headshot.ceiling_extreme is None
            else headshot.ceiling_extreme
        ),
        "skipped": None if accuracy is None else accuracy.skipped,
    }


def _residuals(series: dict[str, dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for player_id, block in series.items():
        pairs = block.get("mirror", [])
        residual = command_residual(
            [row["applied"] for row in pairs],
            [row["command"] for row in pairs],
        )
        out[player_id] = [] if residual is None else [round(value, 4) for value in residual]
    return out


def _positive(samples: list[float]) -> list[float]:
    return [ms for ms in samples if ms > 0]


def _jitter_median(rows: list[dict], unknowable: bool) -> float:
    picked = [
        row["jitter"]
        for row in rows
        if (row["state"] == "unknowable") == unknowable
    ]
    return median(picked)


def _wire_led(rows: list[dict]) -> list[dict]:
    led = []
    for row in rows:
        wire = row["wire"]
        picture = row["picture"]
        delay = row["delay"]
        if delay <= 0 or wire < 0 or picture < 0:
            continue
        if picture - wire < WIRE_MIN_GAP_DEG:
            continue
        if picture <= 0 or wire > WIRE_ERROR_RATIO * picture:
            continue
        led.append(row)
    return led


def _face(players: list[dict], scene: dict) -> list[dict]:
    by_id = {row["id"]: row for row in players}
    weight = by_id["weight-cheat"]["speed"][0]
    blasted = by_id["blasted"]["speed"][0]
    mirror = by_id["mirror-script"]
    late = by_id["late-compensate"]
    know = _jitter_median(by_id["quiet-radar"]["jitter"], unknowable=False)
    unknow = _jitter_median(by_id["quiet-radar"]["jitter"], unknowable=True)
    lock = _positive(by_id["replay-lock"]["private"])
    fight = _positive(by_id["real-fight"]["private"])
    snapped = by_id["wire-lock"]["wire"]
    drawn = by_id["picture-track"]["wire"]
    led = _wire_led(snapped)
    return [
        {
            "tape": "tape-speed",
            "kicker": "Gear",
            "title": f"{weight['weight']:.0f} kg at {weight['speed']:.1f} m/s",
            "detail": (
                f"Cap for that kit is {weight['cap']:.1f}. "
                f"The same speed tagged {blasted['cause']} is excluded."
            ),
            "decision": "review",
        },
        {
            "tape": "tape-mirror",
            "kicker": "Clock",
            "title": f"Same tick r {mirror['mirror_r']:.2f}",
            "detail": (
                f"One shot later the clean pull is {late['mirror_r']:.2f}. A person is late."
            ),
            "decision": "review",
        },
        {
            "tape": "tape-quiet",
            "kicker": "Knowable",
            "title": f"{know:.2f}° drops to {unknow:.2f}°",
            "detail": "The drop exists only while the server says this client could not have known.",
            "decision": "review",
        },
        {
            "tape": "tape-replay",
            "kicker": "Replay",
            "title": f"{scene['heading_deg']:.0f}° heading, {sum(lock):.0f} ms",
            "detail": (
                f"A live route, delayed {scene['delay']} samples. "
                f"The clean fight crosses it for {sum(fight):.0f} ms across {len(fight)} shots."
            ),
            "decision": "review",
        },
        {
            "tape": "tape-wire",
            "kicker": "Picture",
            "title": f"{snapped[0]['wire']:.2f}° wire, {snapped[0]['picture']:.2f}° picture",
            "detail": (
                f"{snapped[0]['delay']:.0f} ms late, {sum(row['delay'] for row in led):.0f} ms across {len(led)} shots. "
                f"The clean crosshair is {drawn[0]['picture']:.2f}° on the picture. The hits match."
            ),
            "decision": "review",
        },
    ]


def _replay_ledes(players: list[dict], scene: dict) -> dict[str, str]:
    by_id = {row["id"]: row for row in players}
    lock = _positive(by_id["replay-lock"]["private"])
    fight = _positive(by_id["real-fight"]["private"])
    heading = f"{scene['heading_deg']:.0f}"
    delay = scene["delay"]
    return {
        "replay-lock": (
            f"Speed, recoil, and aim stay inside the humans. "
            f"The gold path is a live route turned {heading}°, delayed {delay} samples, "
            f"where this client had no sight and no audio. "
            f"The red marks sit on it for {sum(lock):.0f} ms. There is no invisible flag. "
            f"A one-second stay is the review. The public charts are allowed to look boring. "
            f"The scorer reads those milliseconds, not this picture."
        ),
        "real-fight": (
            f"{len(fight)} red marks clip the gold path, {sum(fight):.0f} ms in all. "
            f"That is a spray crossing a volume, not a stay. "
            f"The green marks stay on the enemy this client could see. "
            f"One crossing stays clean. "
            f"A server that marks a visible body as private would manufacture the other card."
        ),
    }


def _wire_ledes(players: list[dict]) -> dict[str, str]:
    by_id = {row["id"]: row for row in players}
    snapped = by_id["wire-lock"]["wire"]
    drawn = by_id["picture-track"]["wire"]
    led = _wire_led(snapped)
    ahead = sum(row["delay"] for row in led)
    return {
        "wire-lock": (
            f"Both players hit the same enemy at the same rate. "
            f"This crosshair is {snapped[0]['wire']:.2f}° off the snapshot the server just sent, "
            f"and {snapped[0]['picture']:.2f}° off the picture the official client draws, "
            f"{snapped[0]['delay']:.0f} ms later. "
            f"That lead holds for {ahead:.0f} ms across {len(led)} shots. The hits match. "
            f"The scorer reads wire_error_deg, picture_error_deg, and interp_delay_ms."
        ),
        "picture-track": (
            f"Same enemy. Same hits. This crosshair is {drawn[0]['picture']:.2f}° off the picture "
            f"and {drawn[0]['wire']:.2f}° off the snapshot. "
            f"The official client draws the picture. A person aims there. "
            f"Standing still would make the two errors match, and nothing would fire. "
            f"A server that scores the wrong timeline manufactures the other card."
        ),
    }


def board_payload(demo: Demo) -> dict:
    series = _series(demo)
    residuals = _residuals(series)
    cases = {case.player_id: case for case in demo.cases}
    rifle = demo.profile.weapons.get("rifle")
    gap_line = None
    if rifle and rifle.min_shot_interval_ms is not None:
        gap_line = rifle.min_shot_interval_ms - rifle.interval_slack_ms
    players = []
    for player_id in TOUR:
        case = cases.get(player_id)
        if case is None:
            continue
        copy = COPY.get(player_id, {})
        block = series.get(
            player_id,
            {
                "speed": [],
                "recoil": [],
                "gaps": [],
                "mirror": [],
                "hidden": [],
                "private": [],
                "jitter": [],
                "wire": [],
            },
        )
        aim = _aim_block(case, demo)
        mirror = block.get("mirror", [])
        same_tick, lagged = _mirror_r(mirror)
        players.append(
            {
                "id": player_id,
                "group": copy.get("group", "Case"),
                "title": copy.get("title", player_id),
                "lede": copy.get("lede", ""),
                "chart": copy.get("chart", "aim"),
                "control": copy.get("control"),
                "control_label": copy.get("control_label"),
                "decision": case.decision,
                "action": case.recommended_action,
                "reports": case.reports,
                "band": case.skill_band,
                "reasons": case.reasons,
                "observations": case.observations[:4],
                "party_note": case.party_note,
                "untrained": case.untrained,
                "speed": block["speed"],
                "recoil": block["recoil"],
                "gaps": block.get("gaps", [])[:80],
                "gap_line": 75 if gap_line is None else gap_line,
                "mirror": mirror,
                "mirror_r": None if same_tick is None else round(same_tick, 2),
                "mirror_lag_r": None if lagged is None else round(lagged, 2),
                "hidden": block.get("hidden", []),
                "private": block.get("private", []),
                "jitter": block.get("jitter", []),
                "wire": block.get("wire", []),
                "residual": residuals.get(player_id, []),
                "residual_twin": residuals.get(case.vendor_twin, []),
                "vendor_twin": case.vendor_twin,
                "vendor_r": case.vendor_r,
                "lags": case.inherit_lags_ms,
                "seal": case.seal,
                "aim": aim,
                "limits": case.limits,
            }
        )
    counts = {"review": 0, "watch": 0, "clean": 0, "insufficient_data": 0}
    for player in players:
        counts[player["decision"]] = counts.get(player["decision"], 0) + 1
    scene = replay_scene()
    ledes = _replay_ledes(players, scene)
    ledes.update(_wire_ledes(players))
    for player in players:
        if player["id"] in ledes:
            player["lede"] = ledes[player["id"]]
    return {
        "game": demo.profile.game_id,
        "counts": counts,
        "light_cap": curve_speed(demo.profile, 3) if demo.profile.weight_classes else None,
        "loaded_cap": curve_speed(demo.profile, 10) if demo.profile.weight_classes else None,
        "gap_line": gap_line,
        "speed_min_run": demo.profile.speed_min_run,
        "voice_ms": demo.profile.voice_min_ms,
        "face": _face(players, scene),
        "replay": scene,
        "players": players,
    }


@lru_cache(maxsize=2)
def _week_ops(seed: int) -> str:
    """The synthetic week through the operations view. Built once per process: it scores eight batches."""
    payload = week_payload(build_week(seed))
    payload["tape_base"] = ""  # the tapes are on this page
    return payload_json(payload)


# The real-match example: the operations view over CS2CD matches, written beside the desk.
# examples/cs2/cs2cd.py desk builds it from a scored run. An installed package has no copy.
CS2_PAYLOAD = ROOT / "examples" / "cs2" / "desk.json"
CS2_PAGE = "cs2.html"
_CS2_TAB = '<a class="tab-link" href="cs2.html"><span>C</span>Real CS2 matches</a>'


def render_board(demo: Demo, week_seed: int = 7) -> str:
    payload = json.dumps(board_payload(demo), separators=(",", ":")).replace("<", "\\u003c")
    shell = (
        _SHELL.replace("/*__OPS_CSS__*/", OPS_CSS)
        .replace("<!--__OPS_HTML__-->", OPS_HTML)
        .replace("/*__OPS_JS__*/", OPS_JS)
        .replace("<!--__CS2_TAB__-->", _CS2_TAB if CS2_PAYLOAD.is_file() else "")
    )
    return shell.replace("/*__OPS__*/", _week_ops(week_seed)).replace("/*__DATA__*/", payload)


def write_cs2_page(folder: str | Path) -> Path | None:
    """The CS2 example beside the desk, linked back to it. None when the example data is absent."""
    if not CS2_PAYLOAD.is_file():
        return None
    payload = json.loads(CS2_PAYLOAD.read_text(encoding="utf-8"))
    target = Path(folder) / CS2_PAGE
    target.write_text(
        render_dashboard(payload, tape_base="board.html", home="board.html", title="Real CS2 matches · fpsdet"),
        encoding="utf-8",
    )
    return target


def write_board(demo: Demo, path: str | Path | None = None) -> Path:
    target = Path(path) if path else Path.cwd() / "demo" / "board.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_board(demo), encoding="utf-8")
    write_cs2_page(target.parent)
    return target


_SHELL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review desk — fpsdet</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="../site/fpsdet.css">
<style>
:root {
  --bg: #040718;
  --panel: #0a102c;
  --panel-hover: #0e1538;
  --elevated: #070b24;
  --plot: #070b24;
  --line: #171e42;
  --line-bright: #242e5e;
  --ink: #e6ebf9;
  --muted: #8a93b8;
  --signal: #38c8ff;
  --violet: #a855f7;
  --review: #ff5470;
  --watch: #d8b26c;
  --clean: #2dd4bf;
  --held: #7c8cff;
  --excluded: #7c8cff;
  --cap: #d8b26c;
  --light: #c9d2f0;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
html, body { margin: 0; color: var(--ink); }
body { position: relative; background: transparent; }
body {
  font: 16px/1.45 var(--sans, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif);
  min-height: 100vh;
}
.mast {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.35rem 1.05rem;
  margin: 0 0 0.9rem;
  padding: 0 0 0.75rem;
  border-bottom: 1px solid var(--line);
  font-size: 0.92rem;
}
.mast { align-items: center; }
.mast .mark {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace);
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  text-decoration: none;
  color: #fff;
  text-shadow: -0.6px 0 rgb(255 84 112 / 0.5), 0.6px 0 rgb(56 200 255 / 0.5);
}
.mast .sig { color: var(--signal); }
.mast .by { margin-right: 0.6rem; font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 11px; color: var(--muted); text-decoration: none; }
.mast a { text-decoration: none; }
header {
  padding: 0.8rem 1.4rem 0.85rem;
  border-bottom: 1px solid var(--line);
}
.topline { display: flex; flex-wrap: wrap; gap: 0.4rem 1.2rem; align-items: baseline; }
.brand { margin: 0; color: rgb(56 200 255 / 0.8); font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); letter-spacing: 0.22em; text-transform: uppercase; font-size: 0.6875rem; }
.counts { margin: 0; color: var(--muted); font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.82rem; }
.counts b { color: var(--ink); font-weight: 500; }
.rule { margin: 0 0 0 auto; }
h1 {
  margin: 0.7rem 0 0.2rem;
  font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace);
  font-size: clamp(2rem, 3.4vw, 3.1rem);
  line-height: 1.02;
  font-weight: 700;
  letter-spacing: -0.05em;
  color: #fff;
  max-width: 16ch;
  text-shadow: -0.035em 0 rgb(255 84 112 / 0.6), 0.035em 0 rgb(56 200 255 / 0.6);
  animation: ca-glitch 7s steps(1, end) infinite;
}
.face {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.55rem;
  margin: 0.9rem 0 0;
}
.face-note {
  margin: 0.55rem 0 0.1rem;
  max-width: 46rem;
  color: var(--muted);
  font-size: 0.84rem;
}
.tour { margin: 0.9rem 0 0.2rem; max-width: 46rem; }
.tour ol { list-style: none; margin: 0.45rem 0 0; padding: 0; display: grid; gap: 0.4rem; }
.tour a.step {
  display: block;
  text-decoration: none;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--line);
  padding: 0.7rem 0;
  color: inherit;
}
.tour a.step:hover { color: var(--ink); }
.tour a.step:focus-visible { outline: 2px solid var(--signal); outline-offset: -2px; }
.tour a.step b { font-weight: 560; }
.tour .more { color: var(--signal); }
#still { scroll-margin-top: 0.8rem; }
button.chip {
  /* A button centers its content. A column keeps every chip's label on one line. */
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  text-align: left;
  background: linear-gradient(180deg, var(--panel), var(--elevated));
  color: inherit;
  border: 1px solid var(--line);
  border-top: 3px solid var(--review);
  border-radius: 0.75rem;
  padding: 0.55rem 0.7rem 0.65rem;
  box-shadow: -1px 0 0 rgb(255 84 112 / 0.22), 1px 0 0 rgb(56 200 255 / 0.22);
  transition: box-shadow 0.25s ease, border-color 0.25s ease;
  cursor: pointer;
  font: inherit;
  min-width: 0;
  overflow-wrap: break-word;
}
button.chip:hover { background: var(--panel-hover); border-color: rgb(56 200 255 / 0.4); border-top-color: var(--review); box-shadow: -2px 0 0 rgb(255 84 112 / 0.45), 2px 0 0 rgb(56 200 255 / 0.45), 0 0 32px -8px rgb(56 200 255 / 0.45); }
button.chip:focus-visible { outline: 2px solid var(--signal); outline-offset: -2px; }
button.chip .kicker { margin: 0; }
button.chip strong {
  display: block;
  margin-top: 0.22rem;
  font-size: 1.02rem;
  font-weight: 560;
  letter-spacing: -0.02em;
  line-height: 1.2;
}
button.chip span {
  display: block;
  margin-top: 0.28rem;
  color: var(--muted);
  font-size: 0.78rem;
  line-height: 1.35;
}
.layout { display: grid; grid-template-columns: 16.5rem 1fr; align-items: start; }
#queue {
  position: sticky;
  top: 0;
  max-height: 100vh;
  overflow: auto;
  border-right: 1px solid var(--line);
  padding: 0.4rem 0 2rem;
}
.group {
  margin: 0.95rem 1rem 0.2rem;
  color: var(--muted);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
button.item {
  display: block;
  width: 100%;
  text-align: left;
  background: transparent;
  color: inherit;
  border: 0;
  border-left: 3px solid transparent;
  padding: 0.42rem 0.8rem 0.42rem 0.75rem;
  cursor: pointer;
  font: inherit;
}
button.item strong { display: block; font-weight: 560; font-size: 0.92rem; }
button.item span { color: var(--muted); font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.72rem; }
button.item:hover { background: #0e1538; }
button.item:focus-visible { outline: 2px solid var(--signal); outline-offset: -2px; }
button.item[aria-current="true"] { background: var(--panel-hover); border-left-color: var(--signal); }
.tag.review { color: var(--review); }
.tag.watch { color: var(--watch); }
.tag.clean { color: var(--clean); }
.tag.insufficient_data { color: var(--held); }
main { padding: 1.15rem 1.35rem 3.5rem; min-width: 0; }
.tape { margin: 0 0 2.6rem; scroll-margin-top: 0.8rem; }
.kicker { margin: 0; color: var(--muted); font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; }
.tape h2 {
  margin: 0.2rem 0 0.35rem;
  font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace);
  font-size: clamp(1.6rem, 2.4vw, 2.15rem);
  line-height: 1.05;
  font-weight: 400;
  letter-spacing: -0.03em;
  max-width: 24ch;
}
.section-lede { margin: 0 0 0.9rem; max-width: 46rem; color: var(--ink); }
.grid { display: grid; gap: 0.75rem; }
.grid.n1 { grid-template-columns: 1fr; }
.grid.n2 { grid-template-columns: 1fr 1fr; }
.grid.n3 { grid-template-columns: 1fr 1fr 1fr; }
.card {
  background: linear-gradient(180deg, var(--panel), var(--elevated));
  border: 1px solid var(--line);
  border-top: 3px solid var(--held);
  border-radius: 0.75rem;
  overflow: hidden;
  min-width: 0;
  scroll-margin-top: 0.8rem;
}
.card.review { border-top-color: var(--review); }
.card.watch { border-top-color: var(--watch); }
.card.clean { border-top-color: var(--clean); }
.card.insufficient_data { border-top-color: var(--held); }
.head { display: flex; justify-content: space-between; gap: 0.8rem; align-items: flex-start; padding: 0.75rem 0.85rem 0.55rem; }
.who { margin: 0; color: var(--muted); font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.75rem; }
.card h3 { margin: 0.2rem 0 0; font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 1.05rem; line-height: 1.3; font-weight: 600; letter-spacing: -0.01em; color: #fff; }
.stamp {
  flex: none;
  font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  padding: 0.22rem 0.5rem;
  border: 1px solid currentColor;
  border-radius: 999px;
  background: color-mix(in srgb, currentColor 8%, transparent);
  box-shadow: 0 0 18px -6px currentColor;
  text-shadow: -0.6px 0 rgb(255 84 112 / 0.55), 0.6px 0 rgb(56 200 255 / 0.55);
}
.stamp.review { color: var(--review); }
.stamp.watch { color: var(--watch); }
.stamp.clean { color: var(--clean); }
.stamp.insufficient_data { color: var(--held); }
.chart { background: var(--plot); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 0.35rem 0.35rem 0; }
svg { width: 100%; height: auto; display: block; }
#stage svg { filter: drop-shadow(-0.7px 0 rgb(255 84 112 / 0.5)) drop-shadow(0.7px 0 rgb(56 200 255 / 0.5)); }
@media (prefers-contrast: more), (forced-colors: active) { #stage svg, h1 { filter: none; text-shadow: none; animation: none; } }
/* Charts are drawn 720 wide. --chart-scale undoes the shrink on a narrow card. */
#stage svg text { font-size: calc(11.5px * var(--chart-scale, 1)); }
.plot { position: relative; }
.plot-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  margin: 0;
  padding: 0 1.2rem;
  color: var(--ink);
  font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace);
  font-size: 0.8rem;
}
.axis-note { margin: 0.35rem 0.45rem 0; color: var(--muted); font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.72rem; }
.legend { display: flex; flex-wrap: wrap; gap: 0.7rem 0.9rem; color: var(--muted); font-size: 0.75rem; padding: 0.35rem 0.45rem 0.5rem; }
.swatch { display: inline-block; width: 0.65rem; height: 0.65rem; margin-right: 0.28rem; vertical-align: -1px; }
.facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.45rem; padding: 0.7rem 0.85rem 0.2rem; }
.fact b {
  display: block;
  font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace);
  font-size: 1.05rem;
  font-weight: 560;
  font-variant-numeric: tabular-nums;
}
.fact span { color: var(--muted); font-size: 0.75rem; }
.finding { margin: 0.35rem 0.85rem 0; font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.8rem; }
.card .lede { margin: 0.45rem 0.85rem 0.8rem; color: var(--muted); font-size: 0.92rem; }
.party { margin: 0 0.85rem 0.8rem; font-size: 0.88rem; }
.seal { margin: 0 0.85rem 0.85rem; color: var(--muted); font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.72rem; letter-spacing: 0.04em; }
.limits { margin: 0.5rem 0 0; color: var(--muted); font-size: 0.88rem; max-width: 46rem; }
.noscript { padding: 1rem 1.4rem; }
@media (max-width: 1100px) {
  .grid.n3 { grid-template-columns: 1fr; }
  .face { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 860px) {
  .rule { margin-left: 0; }
  .mast .mark { flex-basis: 100%; }
  h1 { font-size: 1.55rem; }
  .face { grid-template-columns: 1fr; }
  #still { scroll-margin-top: 5.6rem; }
  .layout { display: block; }
  #queue {
    position: sticky;
    top: 0;
    z-index: 2;
    display: flex;
    gap: 0;
    max-height: none;
    overflow-x: auto;
    background: var(--bg);
    border-right: 0;
    border-bottom: 1px solid var(--line);
    padding: 0;
  }
  .group { display: none; }
  button.item { min-width: 11.5rem; border-left: 0; border-bottom: 3px solid transparent; }
  button.item[aria-current="true"] { border-bottom-color: var(--cap); border-left-color: transparent; }
  .grid.n2, .grid.n3 { grid-template-columns: 1fr; }
  .facts { grid-template-columns: 1fr 1fr 1fr; }
  .tape, .card { scroll-margin-top: 5.6rem; }
}
.deskbar { padding: 0.8rem 1.4rem 0; }
.deskbar .mast { margin: 0; padding: 0 0 0.7rem; border-bottom: 0; }
.tabs { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.25rem; }
.tabs a[role=tab] { padding: 0.55rem 1rem 0.6rem; border: 1px solid transparent; border-bottom: 0; border-radius: 8px 8px 0 0; color: var(--muted); text-decoration: none; font-size: 0.92rem; }
.tabs a[role=tab] span { margin-right: 0.45rem; font-family: var(--mono, ui-monospace, monospace); font-size: 11px; color: var(--signal); }
.tabs a[role=tab]:hover { color: var(--ink); }
.tabs a[role=tab][aria-selected=true] { color: #fff; background: var(--panel); border-color: var(--line); box-shadow: inset 0 2px 0 var(--signal); }
.tabs a[role=tab]:focus-visible { outline: 2px solid var(--signal); outline-offset: -2px; }
.tabs .status-pill { margin: 0 0 0.45rem auto; }
.tabs a.tab-link { padding: 0.55rem 1rem 0.6rem; color: var(--muted); text-decoration: none; font-size: 0.92rem; }
.tabs a.tab-link span { margin-right: 0.45rem; font-family: var(--mono, ui-monospace, monospace); font-size: 11px; color: var(--signal); }
.tabs a.tab-link::after { content: " ↗"; color: var(--signal); }
.tabs a.tab-link:hover { color: var(--ink); }
.tabs a.tab-link:focus-visible { outline: 2px solid var(--signal); outline-offset: -2px; }
#key[hidden] { display: none; }
/* The scanlines are the site's look. Over a chart they stripe the data, so the operations view drops them. */
body.view-ops::after { content: none; }
/*__OPS_CSS__*/
</style>
</head>
<body>
<div class="zb-backdrop" aria-hidden="true"></div>
<header class="deskbar">
  <nav class="mast" aria-label="Site">
    <a class="mark" href="../site/index.html"><span class="pulse-dot" aria-hidden="true"></span><span>fps<span class="sig">det</span></span></a>
    <a class="by" href="https://zerobandwidth.com">by Zero<span class="sig">Bandwidth</span></a>
    <a class="nav-link" href="../site/index.html"><span>01</span>What this is</a>
    <a class="nav-link" href="../site/scoring.html"><span>02</span>Scoring</a>
    <a class="nav-link" href="../site/wire.html"><span>03</span>Wire a game</a>
    <a class="nav-link" href="../site/games.html"><span>04</span>Games</a>
    <a class="nav-link" href="../site/source.html"><span>05</span>Source</a>
    <a class="nav-link" href="#ops" aria-current="page"><span>06</span>Desk</a>
  </nav>
  <div class="tabs" role="tablist" aria-label="Desk views">
    <a role="tab" id="tab-ops" href="#ops" aria-controls="ops" aria-selected="true"><span>A</span>Operations · a synthetic week</a>
    <a role="tab" id="tab-key" href="#key" aria-controls="key" aria-selected="false"><span>B</span>Answer key · 32 planted players</a>
    <!--__CS2_TAB__-->
    <p class="status-pill"><span class="pulse-dot" aria-hidden="true"></span>Automated action: none</p>
  </div>
</header>
<!--__OPS_HTML__-->
<div id="key" role="tabpanel" aria-labelledby="tab-key" hidden>
<header>
  <div class="topline">
    <p class="brand">fpsdet review desk · <span id="game"></span></p>
    <p class="counts" id="counts"></p>
  </div>
  <h1>The answer key.</h1>
  <p class="section-lede">Thirty-two planted players. Expect 11 review, 4 watch, 16 clean, 1 held. The first tape is 10 kg on the ground, running like 3 kg, beside the same sprint tagged as a blast. The blast stays clean. Nothing on this page bans.</p>
  <div id="face" class="face"></div>
  <p class="face-note">These five are computed from the planted cases. The steps under them are the order. A person still reviews.</p>
  <nav class="tour" aria-label="Walk through">
    <p class="kicker">Walk through</p>
    <ol>
      <li><a class="step" href="#tape-speed"><b>1 · Gear.</b> Open the 10 kg sprint. The card beside it is the same speed, tagged as a blast. A blast is not a case.</a></li>
      <li><a class="step" href="#tape-mirror"><b>2 · Clock.</b> The camera still kicks. The command is that kick, backwards, on the same tick. The next card pulls one shot later. That is a person.</a></li>
      <li><a class="step" href="#tape-quiet"><b>3 · A fact this client was not given.</b> The noise drops only while the server says this client could not have known. The same drop, labeled as something they could hear, stays clean.</a></li>
      <li><a class="step" href="#tape-replay"><b>4 · The replay.</b> Someone else's steps, turned onto another heading, where this client has no sight and no audio. One crosshair stays on it. The other only clips it.</a></li>
      <li><a class="step" href="#tape-wire"><b>5 · The picture is late.</b> The snapshot is where the player is now. The official client draws where they were one delay ago. One crosshair sticks to the snapshot. The other sticks to the picture. The hits match.</a></li>
      <li><a class="step" href="#still"><b>6 · What still gets through.</b> A DMA read of the drawn frame, a capture card, a cheat that waits out the delay, a wrong timeline, and a listen server. This does not end cheating.</a></li>
    </ol>
    <p class="face-note">j and k move the tapes. The sidebar is every planted player. The row above is the rest of the site, starting with <a class="more" href="../site/index.html">what this is</a>.</p>
  </nav>
</header>
<div class="layout">
  <nav id="queue" aria-label="Planted players"></nav>
  <main id="stage"></main>
</div>
</div>
<noscript><p class="noscript">This desk draws the queue and the tapes with JavaScript. The terminal printed the same thirty-two decisions.</p></noscript>
<script id="ops-payload" type="application/json">/*__OPS__*/</script>
<script>/*__OPS_JS__*/</script>
<script id="payload" type="application/json">/*__DATA__*/</script>
<script>
const data = JSON.parse(document.getElementById("payload").textContent);
const queue = document.getElementById("queue");
const stage = document.getElementById("stage");
document.getElementById("game").textContent = data.game;
const counts = data.counts;
document.getElementById("counts").innerHTML =
  `<b>${counts.review}</b> to open · <b>${counts.watch}</b> to watch · <b>${counts.clean}</b> clean · <b>${counts.insufficient_data}</b> held`;

const TAPES = [
  {
    id: "tape-speed",
    kicker: "Tape 01 · ground speed",
    title: "Same 7.1 m/s. Same 10 kg kit. One cause is none.",
    lede: "Thirty samples each. The cap for this weight is 5.6 m/s. The lightest kit on this server, about 3 kg, is allowed 7.2. One player held the light speed on the ground. The other held it because the server called the movement an explosion.",
    ids: ["weight-cheat", "blasted"],
    share: true
  },
  {
    id: "tape-traps",
    kicker: "Tape 02 · the same shape, still clean",
    title: "Two wild frames, and a pen the server already allowed.",
    lede: "A speed case is 25 consecutive ground samples with cause none. Two frames stay a note. expected_max_ground_speed_mps is the cap after an adrenaline pen, a perk, or a tac-sprint, and it wins over the weight table.",
    ids: ["glitch", "adrenaline"],
    share: false
  },
  {
    id: "tape-recoil",
    kicker: "Tape 03 · one rifle, the build it actually has",
    title: "No kick on the stock gun. Legal kick once the mods are on.",
    lede: "The floor belongs to the weapon and the mod set. A stock rifle that never climbs is a review. The compensator and grip are a different floor. A gun with no humans yet is listed and left alone.",
    ids: ["no-recoil", "modded-recoil", "new-gun"],
    share: false
  },
  {
    id: "tape-mirror",
    kicker: "Tape 04 · the command, not the camera",
    title: "Legal kick on screen. The command is the kick backwards.",
    lede: "Net pitch stays above the no-recoil floor. The tell is the player command against the kick the server applied. Same tick, and tighter than a one-shot human lag, is a review. The pull that arrives one shot later is a person.",
    ids: ["mirror-script", "late-compensate"],
    share: false
  },
  {
    id: "tape-fire",
    kicker: "Tape 05 · the weapon's own cycle",
    title: "Too fast is one case. Perfectly even is another.",
    lede: "Gaps under 75 ms are faster than this rifle. Gaps of exactly 140 ms, with no variation across a long window, are legal and still not a person. A server-paced gun and tick-quantized stamps stay out of that rule.",
    ids: ["fire-rate", "metronome"],
    share: false
  },
  {
    id: "tape-hidden",
    kicker: "Tape 06 · someone the server had not drawn",
    title: "Tracking a hidden mover. Holding a corner is not that.",
    lede: "hidden_track_ms is time the aim spent on an enemy the server still had hidden. Pre-aiming a corner leaves that field at zero. A visibility query that lies will manufacture the case.",
    ids: ["wall-eye", "angle-holder"],
    share: false
  },
  {
    id: "tape-aim",
    kicker: "Tape 07 · humans already measured",
    title: "Past every elite is a review. Better than your rank is a watch.",
    lede: "Aim uses the lower bound, not the raw percentage. Clearing the best measured human on two rates opens a case. Staying inside those humans, even above your own rank, does not.",
    ids: ["rage", "rank-outlier", "account-changed"],
    share: false
  },
  {
    id: "tape-reports",
    kicker: "Tape 08 · reports are a queue",
    title: "Twenty-five reports. Ordinary aim. A perfect ten is refused.",
    lede: "Reports move a player to the front of the scan. They do not add to the score. Ten shots is too small a window to call anyone superhuman.",
    ids: ["reported-streamer", "small-sample"],
    share: false
  },
  {
    id: "tape-population",
    kicker: "Tape 09 · the baseline",
    title: "Heavy and legal. Elite and ordinary. Weak, and uninteresting.",
    lede: "These are the numbers a cheat has to leave. Low accuracy does not move the score. Sitting on the elite median does not open a case.",
    ids: ["legal-heavy", "elite-human", "weak-human"],
    share: false
  },
  {
    id: "tape-quiet",
    kicker: "Tape 10 · quiet only when they could not know",
    title: "The noise drops when the server has not told this client.",
    lede: "Knowable aim noise is the baseline, including audio. A drop that exists only on unknowable samples is a review. The same drop labeled audio is a person who heard something. A steady hand in both windows is nothing.",
    ids: ["quiet-radar", "listened", "steady-hands"],
    share: false
  },
  {
    id: "tape-vendor",
    kicker: "Tape 11 · the leftover after the kick",
    title: "One customer already reviewed. The next one carries the same leftover.",
    lede: "Remove the server kick and the one-shot lag. What remains is the humanizer. A match puts the second account next in the scan. It does not convict them. Two clean leftovers with no confirmed source stay a watch.",
    ids: ["clone-source", "clone-buyer"],
    share: false
  },
  {
    id: "tape-party",
    kicker: "Tape 12 · faster than a voice",
    title: "The teammate swings the hidden enemy. One of them waited.",
    lede: "wall-eye is already tracking a mover the server had hidden. A teammate who swings that enemy inside 350 ms is a watch. A teammate who swings after a voice could have carried it stays clean.",
    ids: ["radar-friend", "callout-friend"],
    share: false
  },
  {
    id: "tape-replay",
    kicker: "Tape 13 · a body this client was not sent",
    title: "Same movement as a live player. A different heading. One of them stayed on it.",
    lede: "The map is generated from the plant. Gold is a live player's steps on another heading, delayed, inside a volume this client cannot see or hear. Red is this crosshair on that path. Green is this crosshair on the enemy the server did send. Sustained aim on the gold path is a review even when speed, recoil, and aim look ordinary. Four shots that clip it are a crossing. The scorer reads the milliseconds. It does not read this picture.",
    ids: ["replay-lock", "real-fight"],
    share: false
  },
  {
    id: "tape-wire",
    kicker: "Tape 14 · the picture is late",
    title: "Same enemy. Same hits. One crosshair is on the snapshot.",
    lede: "The wire is the quantized position the server just sent. The picture is where the official client draws that player, one interpolation delay earlier. A person aims at the picture. The red line is error to the wire. The blue line is error to the picture. The score reads those two errors and the delay. Standing still makes them match. One shot is not a case. A server that compares against the wrong timeline manufactures the review.",
    ids: ["wire-lock", "picture-track"],
    share: false
  }
];

const byId = Object.fromEntries(data.players.map(player => [player.id, player]));
const tapeIds = TAPES.map(tape => tape.id);

// The tape at the top of the screen, read from the scroll position so a mouse
// or a sidebar click never leaves j and k behind. -1 is above the first tape.
function tapeHere() {
  let here = -1;
  tapeIds.forEach((id, index) => {
    const node = document.getElementById(id);
    if (!node) return;
    const margin = parseFloat(getComputedStyle(node).scrollMarginTop) || 0;
    if (node.getBoundingClientRect().top - margin <= 8) here = index;
  });
  return here;
}

function el(tag, attrs, text) {
  const node = document.createElement(tag);
  if (attrs) Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (text != null) node.textContent = text;
  return node;
}
function stampWord(decision) {
  return decision === "insufficient_data" ? "HELD" : decision.toUpperCase();
}
function svgEl(name, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  if (attrs) Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text != null) node.textContent = text;
  return node;
}
function chartFrame(h, label) {
  const svg = svgEl("svg", {viewBox: `0 0 720 ${h}`, role: "img"});
  svg.append(svgEl("title", {}, label));
  svg.append(svgEl("rect", {x: 0, y: 0, width: 720, height: h, fill: "none"}));
  return svg;
}
// A sentence does not fit a 720-wide frame on a phone. It goes under the plot,
// or over an empty one, as HTML that wraps. The SVG keeps the short labels.
function axisNote(svg, text) {
  svg.dataset.note = text;
}
function emptyNote(svg, text) {
  svg.dataset.empty = text;
}
function monoText(svg, x, y, text, fill, anchor) {
  svg.append(svgEl("text", {
    x, y, fill, "text-anchor": anchor || "start", "font-size": 11,
    "font-family": "ui-monospace, Menlo, Consolas, monospace"
  }, text));
}
function guideLines(player) {
  const used = player.speed.find(row => row.cap != null);
  const usedCap = used ? used.cap : null;
  const lines = [];
  if (usedCap != null) lines.push({v: usedCap, color: "#d8b26c", dash: "", label: "cap " + usedCap.toFixed(1)});
  if (data.loaded_cap != null && usedCap != null && Math.abs(data.loaded_cap - usedCap) > 0.05) {
    lines.push({v: data.loaded_cap, color: "#8a93b8", dash: "5 4", label: "10 kg " + data.loaded_cap.toFixed(1)});
  }
  const nearLight = data.light_cap != null && player.speed.some(row => row.speed > data.light_cap * 0.9);
  if (nearLight && usedCap != null && Math.abs(data.light_cap - usedCap) > 0.05) {
    lines.push({v: data.light_cap, color: "#c9d2f0", dash: "2 3", label: "3 kg " + data.light_cap.toFixed(1)});
  }
  return lines;
}
function speedChart(player, sharedMax) {
  const rows = player.speed;
  const h = 300, padL = 46, padR = 16, padT = 18, padB = 28;
  const svg = chartFrame(h, player.id + " ground speed");
  if (!rows.length) {
    emptyNote(svg, "No movement samples on this player.");
    return svg;
  }
  const speeds = rows.map(row => row.speed);
  const caps = rows.map(row => row.cap || 0);
  let maxY = sharedMax;
  if (maxY == null) maxY = Math.max(8, data.light_cap || 0, data.loaded_cap || 0, ...speeds, ...caps) * 1.12;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const x = i => padL + (rows.length === 1 ? innerW / 2 : innerW * i / (rows.length - 1));
  const y = v => padT + innerH * (1 - v / maxY);
  const step = maxY > 12 ? 5 : 2;
  for (let tick = 0; tick <= maxY; tick += step) {
    svg.append(svgEl("line", {x1: padL, x2: 704, y1: y(tick), y2: y(tick), stroke: "#242e5e", "stroke-width": 1}));
    monoText(svg, padL - 6, y(tick) + 4, String(tick), "#8a93b8", "end");
  }
  const guides = guideLines(player).sort((a, b) => b.v - a.v);
  let lastLabel = -999;
  guides.forEach(guide => {
    const attrs = {x1: padL, x2: 704, y1: y(guide.v), y2: y(guide.v), stroke: guide.color, "stroke-width": 1.5};
    if (guide.dash) attrs["stroke-dasharray"] = guide.dash;
    svg.append(svgEl("line", attrs));
    let labelY = y(guide.v) - 5;
    if (labelY < lastLabel + 12) labelY = lastLabel + 12;
    lastLabel = labelY;
    monoText(svg, 700, labelY, guide.label, guide.color, "end");
  });
  rows.forEach((row, i) => {
    const color = row.cause !== "none" ? "#7c8cff" : (row.over ? "#ff5470" : "#2dd4bf");
    if (i) {
      const prev = rows[i - 1];
      const prevColor = prev.cause !== "none" ? "#7c8cff" : (prev.over ? "#ff5470" : "#2dd4bf");
      svg.append(svgEl("line", {
        x1: x(i - 1), y1: y(prev.speed), x2: x(i), y2: y(row.speed),
        stroke: prevColor, "stroke-width": 1.6
      }));
    }
    svg.append(svgEl("circle", {cx: x(i), cy: y(row.speed), r: row.over || row.cause !== "none" ? 3.6 : 2.8, fill: color}));
  });
  axisNote(svg, "each mark is one server sample");
  return svg;
}
function recoilChart(player) {
  const rows = player.recoil;
  const h = 280, padL = 44, padR = 16, padT = 22, padB = 28;
  const svg = chartFrame(h, player.id + " recoil");
  if (!rows.length) {
    emptyNote(svg, "No recoil samples on this player.");
    return svg;
  }
  const floor = rows.find(row => row.floor != null);
  const floorV = floor ? floor.floor : null;
  const maxY = Math.max(2.2, floorV || 0, ...rows.map(row => row.pitch)) * 1.2;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const x = i => padL + innerW * i / Math.max(1, rows.length - 1);
  const y = v => padT + innerH * (1 - Math.max(0, v) / maxY);
  if (floorV == null) {
    monoText(svg, padL, 18, "no floor on file for this build", "#8a93b8");
  } else {
    const blatant = floorV * 0.25;
    svg.append(svgEl("rect", {x: padL, y: y(blatant), width: innerW, height: Math.max(0, y(0) - y(blatant)), fill: "#ff5470", opacity: 0.14}));
    svg.append(svgEl("line", {x1: padL, x2: 704, y1: y(floorV), y2: y(floorV), stroke: "#d8b26c", "stroke-dasharray": "5 4"}));
    monoText(svg, 700, y(floorV) - 6, "floor " + floorV.toFixed(1) + "°", "#d8b26c", "end");
  }
  let d = "";
  rows.forEach((row, i) => { d += (i ? "L" : "M") + x(i).toFixed(1) + " " + y(row.pitch).toFixed(1) + " "; });
  svg.append(svgEl("path", {d, fill: "none", stroke: "#e6ebf9", "stroke-width": 1.7}));
  axisNote(svg, "spray index, from the first shot");
  return svg;
}
function gapChart(player) {
  const gaps = player.gaps;
  const h = 200, padL = 44, padR = 12, padT = 18, padB = 26;
  const svg = chartFrame(h, player.id + " shot gaps");
  if (!gaps.length) {
    emptyNote(svg, "No shot gaps on this player.");
    return svg;
  }
  const line = player.gap_line || data.gap_line || 75;
  const maxY = Math.max(line, ...gaps) * 1.22;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const y = v => padT + innerH * (1 - v / maxY);
  const barW = innerW / gaps.length;
  gaps.forEach((gap, i) => {
    const top = y(gap);
    svg.append(svgEl("rect", {
      x: padL + i * barW + 0.6,
      y: top,
      width: Math.max(1, barW - 1.2),
      height: Math.max(0, y(0) - top),
      fill: gap < line ? "#ff5470" : "#2dd4bf"
    }));
  });
  // The legal line goes over the bars, or a legal cycle hides it.
  svg.append(svgEl("line", {x1: padL, x2: 708, y1: y(line), y2: y(line), stroke: "#d8b26c", "stroke-width": 2}));
  const labelY = y(line) - 6;
  svg.append(svgEl("rect", {x: 552, y: labelY - 19, width: 154, height: 25, fill: "#070b24", opacity: 0.85}));
  monoText(svg, 700, labelY, line + " ms legal", "#d8b26c", "end");
  axisNote(svg, "milliseconds between server-accepted shots");
  return svg;
}
function poly(svg, rows, x, y, key, color) {
  let d = "";
  rows.forEach((row, i) => { d += (i ? "L" : "M") + x(i).toFixed(1) + " " + y(row[key]).toFixed(1) + " "; });
  svg.append(svgEl("path", {d, fill: "none", stroke: color, "stroke-width": 1.6}));
}
function wireChart(player) {
  const rows = player.wire || [];
  const h = 240, padL = 44, padR = 16, padT = 22, padB = 28;
  const svg = chartFrame(h, player.id + " error to the wire and to the picture");
  if (!rows.length) {
    emptyNote(svg, "No wire and picture errors on these shots.");
    return svg;
  }
  let maxY = 0.4;
  rows.forEach(row => { maxY = Math.max(maxY, row.wire, row.picture); });
  maxY *= 1.18;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const x = i => padL + innerW * i / Math.max(1, rows.length - 1);
  const y = v => padT + innerH * (1 - v / maxY);
  svg.append(svgEl("line", {x1: padL, x2: 704, y1: y(0), y2: y(0), stroke: "#242e5e"}));
  poly(svg, rows, x, y, "wire", "#ff5470");
  poly(svg, rows, x, y, "picture", "#7c8cff");
  axisNote(svg, "degrees off the snapshot, and off the picture the client draws");
  return svg;
}
function mirrorChart(player) {
  const rows = player.mirror || [];
  const h = 280, padL = 44, padR = 16, padT = 22, padB = 28;
  const svg = chartFrame(h, player.id + " command against server kick");
  if (!rows.length) {
    emptyNote(svg, "No server kick and player command on these shots.");
    return svg;
  }
  let maxAbs = 2.2;
  rows.forEach(row => {
    maxAbs = Math.max(maxAbs, Math.abs(row.applied), Math.abs(row.command), Math.abs(row.net));
  });
  maxAbs *= 1.15;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const x = i => padL + innerW * i / Math.max(1, rows.length - 1);
  const y = v => padT + innerH * (1 - (v + maxAbs) / (2 * maxAbs));
  svg.append(svgEl("line", {x1: padL, x2: 704, y1: y(0), y2: y(0), stroke: "#242e5e", "stroke-width": 1}));
  monoText(svg, padL - 6, y(0) + 4, "0", "#8a93b8", "end");
  poly(svg, rows, x, y, "applied", "#d8b26c");
  poly(svg, rows, x, y, "command", "#ff5470");
  poly(svg, rows, x, y, "net", "#e6ebf9");
  axisNote(svg, "degrees, shot by shot. Zero is no pitch.");
  return svg;
}
function msBars(samples, label, empty, axis, hot) {
  const h = 200, padL = 44, padR = 12, padT = 18, padB = 26;
  const svg = chartFrame(h, label);
  const total = samples.reduce((sum, ms) => sum + ms, 0);
  if (!samples.length || total <= 0) {
    emptyNote(svg, empty);
    return svg;
  }
  const maxY = Math.max(...samples) * 1.25;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const y = v => padT + innerH * (1 - v / maxY);
  const barW = innerW / samples.length;
  samples.forEach((ms, i) => {
    const top = y(ms);
    svg.append(svgEl("rect", {
      x: padL + i * barW + 0.6,
      y: top,
      width: Math.max(1, barW - 1.2),
      height: Math.max(0, y(0) - top),
      fill: ms > 0 ? hot : "#242e5e"
    }));
  });
  axisNote(svg, axis);
  return svg;
}
function hiddenChart(player) {
  return msBars(
    player.hidden || [],
    player.id + " time on a hidden mover",
    "No time on an enemy the server still had hidden.",
    "milliseconds the aim cone held a hidden mover",
    "#ff5470"
  );
}
function mapPath(svg, points, X, Y, color, width) {
  if (!points.length) return;
  let d = "";
  points.forEach((point, i) => {
    d += (i ? "L" : "M") + X(point[0]).toFixed(1) + " " + Y(point[1]).toFixed(1) + " ";
  });
  svg.append(svgEl("path", {d, fill: "none", stroke: color, "stroke-width": width, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  const last = points[points.length - 1];
  svg.append(svgEl("circle", {cx: X(last[0]).toFixed(1), cy: Y(last[1]).toFixed(1), r: 3.4, fill: color}));
}
function privateChart(player) {
  const scene = data.replay;
  const h = 390;
  const svg = chartFrame(h, player.id + " private replay, generated from the plant");
  if (!scene || !scene.source || !scene.source.length) {
    emptyNote(svg, "No generated route on this desk.");
    return svg;
  }
  const aims = (scene.aims && scene.aims[player.id]) || [];
  const samples = player.private || [];
  const wall = scene.wall;
  const cloud = scene.source.concat(scene.replay, scene.enemy, aims);
  let minX = wall.x, maxX = wall.x + wall.w, minY = wall.y, maxY = wall.y + wall.h;
  cloud.forEach(point => {
    minX = Math.min(minX, point[0]);
    maxX = Math.max(maxX, point[0]);
    minY = Math.min(minY, point[1]);
    maxY = Math.max(maxY, point[1]);
  });
  const margin = 3.2;
  minX -= margin;
  maxX += margin;
  minY -= margin;
  maxY += margin;
  const padL = 18, padR = 18, padT = 22, padB = 28;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const scale = Math.min(innerW / (maxX - minX), innerH / (maxY - minY));
  const usedW = scale * (maxX - minX);
  const usedH = scale * (maxY - minY);
  const ox = padL + (innerW - usedW) / 2;
  const oy = padT + (innerH - usedH) / 2;
  const X = x => ox + (x - minX) * scale;
  const Y = y => oy + (maxY - y) * scale;
  svg.append(svgEl("rect", {
    x: X(wall.x), y: Y(wall.y + wall.h), width: wall.w * scale, height: wall.h * scale,
    fill: "#d8b26c", opacity: 0.1, stroke: "#d8b26c", "stroke-dasharray": "4 3", "stroke-width": 1
  }));
  mapPath(svg, scene.source, X, Y, "#6d77a3", 1.6);
  mapPath(svg, scene.enemy, X, Y, "#7c8cff", 1.8);
  mapPath(svg, scene.replay, X, Y, "#d8b26c", 2.4);
  const hotPts = [];
  const coldPts = [];
  aims.forEach((point, i) => (samples[i] > 0 ? hotPts : coldPts).push(point));
  mapPath(svg, coldPts, X, Y, "#2dd4bf", 2.2);
  mapPath(svg, hotPts, X, Y, "#ff5470", 3.2);
  aims.forEach((point, i) => {
    const hot = samples[i] > 0;
    svg.append(svgEl("circle", {
      cx: X(point[0]).toFixed(1),
      cy: Y(point[1]).toFixed(1),
      r: hot ? 3.2 : 2.2,
      fill: hot ? "#ff5470" : "#2dd4bf"
    }));
  });
  const placed = [];
  const label = (x, y, text, fill, anchor) => {
    const half = anchor === "middle" ? text.length * 6.5 : 0;
    let px = Math.max(14 + half, Math.min(700 - half, x));
    let py = Math.max(20, Math.min(h - 32, y));
    placed.forEach(prev => {
      if (Math.abs(prev.y - py) < 24 && Math.abs(prev.x - px) < 220) py = Math.min(h - 32, prev.y + 26);
    });
    placed.push({x: px, y: py});
    monoText(svg, px, py, text, fill, anchor);
  };
  const sourceMid = scene.source[Math.floor(scene.source.length / 2)];
  const enemyMid = scene.enemy[Math.floor(scene.enemy.length / 2)];
  label(X(sourceMid[0]), Y(sourceMid[1]) + 20, "live route", "#a9b2d6", "middle");
  label(X(enemyMid[0]), Y(enemyMid[1]) + 22, "seen enemy", "#7c8cff", "middle");
  label(X(wall.x + wall.w / 2), Y(wall.y + wall.h) - 34, "replay " + Math.round(scene.heading_deg) + "°", "#d8b26c", "middle");
  label(X(wall.x + wall.w / 2), Y(wall.y + wall.h) - 8, "no sight, no audio", "#d8b26c", "middle");
  axisNote(svg, "generated from the plant. the score reads milliseconds, not this map.");
  return svg;
}
function jitterChart(player) {
  const rows = player.jitter || [];
  const h = 200, padL = 44, padR = 12, padT = 18, padB = 26;
  const svg = chartFrame(h, player.id + " aim noise by what the client could know");
  if (!rows.length) {
    emptyNote(svg, "No aim noise labeled with what this client could know.");
    return svg;
  }
  const maxY = Math.max(0.2, ...rows.map(row => row.jitter)) * 1.25;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const y = v => padT + innerH * (1 - v / maxY);
  const barW = innerW / rows.length;
  const color = state => state === "unknowable" ? "#ff5470" : (state === "audio" ? "#7c8cff" : "#2dd4bf");
  rows.forEach((row, i) => {
    const top = y(row.jitter);
    svg.append(svgEl("rect", {
      x: padL + i * barW + 0.4,
      y: top,
      width: Math.max(1, barW - 0.8),
      height: Math.max(0, y(0) - top),
      fill: color(row.state)
    }));
  });
  axisNote(svg, "degrees of aim noise, in server order");
  return svg;
}
function centered(values) {
  const n = values.length;
  const mean = values.reduce((sum, value) => sum + value, 0) / n;
  let scatter = 0;
  values.forEach(value => { const delta = value - mean; scatter += delta * delta; });
  const scale = Math.sqrt(scatter / Math.max(1, n - 1)) || 1;
  return values.map(value => (value - mean) / scale);
}
function residualChart(player) {
  const mine = player.residual || [];
  const twin = player.residual_twin || [];
  const n = Math.min(mine.length, twin.length);
  const h = 280, padL = 48, padR = 16, padT = 18, padB = 28;
  const svg = chartFrame(h, player.id + " leftover against the other customer");
  if (n < 3) {
    emptyNote(svg, "No leftover series long enough to compare.");
    return svg;
  }
  const xs = centered(mine.slice(0, n));
  const ys = centered(twin.slice(0, n));
  let maxAbs = 2.2;
  for (let i = 0; i < n; i++) maxAbs = Math.max(maxAbs, Math.abs(xs[i]), Math.abs(ys[i]));
  maxAbs *= 1.08;
  const inner = 720 - padL - padR;
  const plot = h - padT - padB;
  const x = v => padL + inner * (v + maxAbs) / (2 * maxAbs);
  const y = v => padT + plot * (1 - (v + maxAbs) / (2 * maxAbs));
  svg.append(svgEl("line", {x1: x(-maxAbs), y1: y(-maxAbs), x2: x(maxAbs), y2: y(maxAbs), stroke: "#242e5e", "stroke-width": 1.4}));
  for (let i = 0; i < n; i++) {
    svg.append(svgEl("circle", {cx: x(xs[i]), cy: y(ys[i]), r: 2.4, fill: "#d8b26c"}));
  }
  axisNote(svg, "this leftover, scaled  →");
  monoText(svg, 704, 16, (player.vendor_twin || "twin") + " ↑", "#8a93b8", "end");
  return svg;
}
function lagChart(player) {
  const lags = player.lags || [];
  const h = 200, padL = 44, padR = 12, padT = 18, padB = 26;
  const svg = chartFrame(h, player.id + " lag behind the teammate who already knew");
  const voice = data.voice_ms || 350;
  if (!lags.length) {
    emptyNote(svg, "No swing on an enemy a teammate was tracking while hidden.");
    return svg;
  }
  const maxY = Math.max(voice, ...lags) * 1.18;
  const innerW = 720 - padL - padR;
  const innerH = h - padT - padB;
  const y = v => padT + innerH * (1 - v / maxY);
  svg.append(svgEl("line", {x1: padL, x2: 708, y1: y(voice), y2: y(voice), stroke: "#d8b26c", "stroke-width": 1.5}));
  monoText(svg, 700, y(voice) - 6, voice + " ms voice", "#d8b26c", "end");
  const barW = innerW / lags.length;
  lags.forEach((lag, i) => {
    const top = y(lag);
    svg.append(svgEl("rect", {
      x: padL + i * barW + 0.6,
      y: top,
      width: Math.max(1, barW - 1.2),
      height: Math.max(0, y(0) - top),
      fill: lag < voice ? "#ff5470" : "#2dd4bf"
    }));
  });
  axisNote(svg, "milliseconds after the teammate's hidden track");
  return svg;
}
function track(svg, y0, bound, human, rank, maxX) {
  const x = v => 168 + 520 * (v / maxX);
  svg.append(svgEl("line", {x1: 168, x2: 688, y1: y0, y2: y0, stroke: "#242e5e", "stroke-width": 8, "stroke-linecap": "round"}));
  let playerColor = "#2dd4bf";
  if (human != null && bound > human) playerColor = "#ff5470";
  else if (rank != null && bound > rank) playerColor = "#e6ebf9";
  const marks = [];
  if (rank != null) marks.push({v: rank, color: "#8a93b8", where: "high"});
  if (human != null) marks.push({v: human, color: "#d8b26c", where: "high"});
  marks.push({v: bound, color: playerColor, where: "low"});
  marks.forEach(mark => {
    const px = x(mark.v);
    svg.append(svgEl("line", {x1: px, x2: px, y1: y0 - 16, y2: y0 + 16, stroke: mark.color, "stroke-width": 2.4}));
  });
  const used = [];
  marks.forEach(mark => {
    let lx = x(mark.v);
    const ly = mark.where === "high" ? y0 - 22 : y0 + 32;
    const crowded = used.some(prev => prev.where === mark.where && Math.abs(prev.lx - lx) < 46);
    if (crowded && mark.color === "#8a93b8") return;
    if (crowded) lx += 28;
    used.push({lx, ly, where: mark.where});
    monoText(svg, lx, ly, Math.round(mark.v * 100) + "%", mark.color, "middle");
  });
}
function aimChart(player) {
  const aim = player.aim;
  const h = aim.headshot_bound != null && aim.elite_headshot_max != null ? 250 : 200;
  const svg = chartFrame(h, player.id + " aim");
  const bound = aim.accuracy_bound;
  const human = aim.elite_accuracy_max;
  if (bound == null || human == null) {
    const why = (player.observations && player.observations[0]) || aim.skipped || "Not enough shots, or no cohort for this weapon yet.";
    emptyNote(svg, why);
    return svg;
  }
  const rank = aim.rank_p95;
  let maxX = Math.max(bound, human, rank || 0);
  if (aim.headshot_bound != null && aim.elite_headshot_max != null) {
    maxX = Math.max(maxX, aim.headshot_bound, aim.elite_headshot_max, aim.headshot_rank_p95 || 0);
  }
  maxX *= 1.08;
  monoText(svg, 28, 36, "Accuracy", "#8a93b8");
  track(svg, 86, bound, human, rank, maxX);
  if (aim.headshot_bound != null && aim.elite_headshot_max != null) {
    monoText(svg, 28, 156, "Headshots", "#8a93b8");
    track(svg, 200, aim.headshot_bound, aim.elite_headshot_max, aim.headshot_rank_p95, maxX);
  }
  return svg;
}
function pct(value) {
  return value == null ? "—" : Math.round(value * 100) + "%";
}
function factsFor(player) {
  const wrap = el("div", {class: "facts"});
  const add = (big, small) => {
    const node = el("div", {class: "fact"});
    node.append(el("b", null, big));
    node.append(el("span", null, small));
    wrap.append(node);
  };
  if (player.chart === "speed" && player.speed.length) {
    const weight = player.speed.find(row => row.weight != null);
    const cap = player.speed.find(row => row.cap != null);
    const speeds = [...new Set(player.speed.map(row => row.speed))];
    const held = (speeds.length <= 2 ? speeds.map(v => v.toFixed(1)).join(" then ") : speeds[0].toFixed(1)) + " m/s";
    const over = player.speed.filter(row => row.over).length;
    const excluded = player.speed.filter(row => row.cause !== "none").length;
    add(weight ? weight.weight.toFixed(0) + " kg" : "—", "loadout weight");
    add(held, "server speed");
    add(excluded ? excluded + " excluded" : over + " over", excluded ? "tagged, not scored" : "samples over " + (cap && cap.cap != null ? cap.cap.toFixed(1) : "cap"));
  } else if (player.chart === "recoil" && player.recoil.length) {
    const floor = player.recoil.find(row => row.floor != null);
    const pitches = player.recoil.map(row => row.pitch).slice().sort((a, b) => a - b);
    const med = pitches[Math.floor(pitches.length / 2)];
    add(floor && floor.floor != null ? floor.floor.toFixed(1) + "°" : "none", "floor for this build");
    add(med.toFixed(2) + "°", "pitch in this spray");
    add(player.untrained.length ? "untrained" : (player.decision === "review" ? "break" : "inside"), "what the desk did");
  } else if (player.chart === "gaps" && player.gaps.length) {
    const line = player.gap_line || 75;
    const under = player.gaps.filter(gap => gap < line).length;
    add(line + " ms", "minimum legal gap");
    add(under + " / " + player.gaps.length, "gaps under that line");
    add(Math.min(...player.gaps) + " ms", "shortest gap");
  } else if (player.chart === "mirror" && player.mirror && player.mirror.length) {
    const nets = player.mirror.map(row => row.net).slice().sort((a, b) => a - b);
    const med = nets[Math.floor(nets.length / 2)];
    add(player.mirror_r == null ? "—" : player.mirror_r.toFixed(2), "same-tick r");
    add(player.mirror_lag_r == null ? "—" : player.mirror_lag_r.toFixed(2), "one shot later");
    add(med.toFixed(2) + "°", "camera, net of the kick");
  } else if (player.chart === "hidden") {
    const samples = player.hidden || [];
    const live = samples.filter(ms => ms > 0);
    const total = live.reduce((sum, ms) => sum + ms, 0);
    add(Math.round(total) + " ms", "on a hidden mover");
    add(String(live.length), "shots with that time");
    add(live.length ? "server" : "none", "visibility time");
  } else if (player.chart === "private") {
    const samples = player.private || [];
    const live = samples.filter(ms => ms > 0);
    const total = live.reduce((sum, ms) => sum + ms, 0);
    add(Math.round(total) + " ms", "on the private replay");
    add(String(live.length), "shots on that path");
    add(live.length >= 8 && total >= 1200 ? "sustained" : "a crossing", "what the desk did");
  } else if (player.chart === "wire" && player.wire && player.wire.length) {
    const rows = player.wire;
    const led = rows.filter(row => row.delay > 0 && row.picture - row.wire >= 0.20 && row.picture > 0 && row.wire <= 0.35 * row.picture);
    const ahead = led.reduce((sum, row) => sum + row.delay, 0);
    add(rows[0].wire.toFixed(2) + "°", "error to the wire");
    add(rows[0].picture.toFixed(2) + "°", "error to the picture");
    add(Math.round(ahead) + " ms", "ahead, across " + led.length + " shots");
  } else if (player.chart === "jitter" && player.jitter && player.jitter.length) {
    const know = player.jitter.filter(row => row.state !== "unknowable").map(row => row.jitter).sort((a, b) => a - b);
    const hidden = player.jitter.filter(row => row.state === "unknowable").map(row => row.jitter).sort((a, b) => a - b);
    const mid = list => list.length ? list[Math.floor(list.length / 2)].toFixed(2) + "°" : "—";
    add(mid(know), "knowable noise");
    add(mid(hidden), "unknowable noise");
    add(String(hidden.length), "unknowable samples");
  } else if (player.chart === "residual") {
    add(player.vendor_r == null ? "—" : player.vendor_r.toFixed(2), "leftover r");
    add(player.vendor_twin || "—", "other customer");
    add(String((player.residual || []).length), "shots in the leftover");
  } else if (player.chart === "lags") {
    const lags = player.lags || [];
    const voice = data.voice_ms || 350;
    const fast = lags.filter(lag => lag < voice);
    add(lags.length ? Math.min(...lags) + " ms" : "—", "fastest swing");
    add(voice + " ms", "voice needs at least");
    add(fast.length + " / " + lags.length, "swings inside that");
  } else {
    const aim = player.aim;
    add(pct(aim.accuracy_bound), "accuracy, lower bound");
    add(pct(aim.elite_accuracy_max), "best elite measured");
    add(aim.headshot_bound == null ? String(player.reports) : pct(aim.headshot_bound), aim.headshot_bound == null ? "player reports" : "headshots, lower bound");
  }
  return wrap;
}
function legendFor(player) {
  const row = el("div", {class: "legend"});
  const item = (color, text) => {
    const span = el("span");
    const box = el("i", {class: "swatch"});
    box.style.background = color;
    span.append(box, document.createTextNode(text));
    row.append(span);
  };
  if (player.chart === "speed") {
    item("#ff5470", "over the cap, cause none");
    item("#2dd4bf", "inside the cap");
    item("#7c8cff", "excluded, blast or other cause");
    item("#d8b26c", "cap the server enforced");
    item("#c9d2f0", "3 kg cap, when the sprint is up there");
  } else if (player.chart === "recoil") {
    item("#e6ebf9", "measured kick");
    item("#d8b26c", "floor for this build");
    item("#ff5470", "under a quarter of that floor");
  } else if (player.chart === "gaps") {
    item("#ff5470", "faster than the weapon");
    item("#2dd4bf", "legal gap");
    item("#d8b26c", "cycle minus one tick");
  } else if (player.chart === "mirror") {
    item("#d8b26c", "kick the server applied");
    item("#ff5470", "player command");
    item("#e6ebf9", "camera, kick plus command");
  } else if (player.chart === "hidden") {
    item("#ff5470", "time on an enemy still hidden");
  } else if (player.chart === "private") {
    const heading = data.replay ? Math.round(data.replay.heading_deg) : "";
    item("#d8b26c", "replay, turned " + heading + "°");
    item("#6d77a3", "the live route it was copied from");
    item("#7c8cff", "the enemy this client could see");
    item("#ff5470", "this crosshair, on the replay");
    item("#2dd4bf", "this crosshair, on that enemy");
  } else if (player.chart === "wire") {
    item("#ff5470", "error to the snapshot");
    item("#7c8cff", "error to the picture");
  } else if (player.chart === "jitter") {
    item("#2dd4bf", "visible, the client could know");
    item("#7c8cff", "audio, the client could know");
    item("#ff5470", "unknowable, no sight and no audio");
  } else if (player.chart === "residual") {
    item("#d8b26c", "this leftover against the other customer");
    item("#242e5e", "a match sits on the diagonal");
  } else if (player.chart === "lags") {
    item("#ff5470", "faster than a voice");
    item("#2dd4bf", "late enough to have been told");
    item("#d8b26c", "350 ms, a voice");
  } else if (player.aim.accuracy_bound != null) {
    item("#8a93b8", "this rank's p95");
    item("#d8b26c", "best measured elite");
    item("#ff5470", "this player, past that elite");
    item("#e6ebf9", "this player, above this rank only");
    item("#2dd4bf", "this player, inside the humans");
  }
  return row;
}
function cardFor(player, sharedMax) {
  const card = el("article", {class: "card " + player.decision, id: "card-" + player.id});
  const head = el("div", {class: "head"});
  const titles = el("div");
  const who = player.id + (player.reports ? " · " + player.reports + " reports" : "") + " · " + player.band;
  titles.append(el("p", {class: "who"}, who));
  titles.append(el("h3", null, player.title));
  head.append(titles);
  head.append(el("div", {class: "stamp " + player.decision}, stampWord(player.decision)));
  card.append(head);
  const box = el("div", {class: "chart"});
  const drawer = player.chart === "speed" ? speedChart(player, sharedMax)
    : player.chart === "recoil" ? recoilChart(player)
    : player.chart === "gaps" ? gapChart(player)
    : player.chart === "mirror" ? mirrorChart(player)
    : player.chart === "hidden" ? hiddenChart(player)
    : player.chart === "private" ? privateChart(player)
    : player.chart === "wire" ? wireChart(player)
    : player.chart === "jitter" ? jitterChart(player)
    : player.chart === "residual" ? residualChart(player)
    : player.chart === "lags" ? lagChart(player)
    : aimChart(player);
  const plot = el("div", {class: "plot"});
  plot.append(drawer);
  if (drawer.dataset.empty) plot.append(el("p", {class: "plot-empty"}, drawer.dataset.empty));
  box.append(plot);
  if (drawer.dataset.note) box.append(el("p", {class: "axis-note"}, drawer.dataset.note));
  box.append(legendFor(player));
  card.append(box);
  card.append(factsFor(player));
  const lines = player.reasons.length ? player.reasons : player.observations.slice(0, 2);
  lines.forEach(text => card.append(el("p", {class: "finding"}, text.replace(/([A-Za-z0-9_-]+)\|/g, "$1"))));
  if (player.lede) card.append(el("p", {class: "lede"}, player.lede));
  if (player.party_note) card.append(el("p", {class: "party"}, player.party_note));
  if (player.seal) card.append(el("p", {class: "seal"}, "seal " + player.seal.slice(0, 16)));
  return card;
}
function sharedSpeed(ids) {
  let maxY = Math.max(8, data.light_cap || 0, data.loaded_cap || 0);
  ids.forEach(id => {
    const player = byId[id];
    if (!player) return;
    player.speed.forEach(row => { maxY = Math.max(maxY, row.speed, row.cap || 0); });
  });
  return maxY * 1.12;
}
function paintFace() {
  const face = document.getElementById("face");
  if (!face || !data.face) return;
  face.replaceChildren();
  data.face.forEach(chip => {
    const button = el("button", {type: "button", class: "chip"});
    button.append(el("p", {class: "kicker"}, chip.kicker));
    button.append(el("strong", null, chip.title));
    button.append(el("span", null, chip.detail));
    button.addEventListener("click", () => {
      const node = document.getElementById(chip.tape);
      if (node) node.scrollIntoView({block: "start"});
    });
    face.append(button);
  });
}
function paint() {
  paintFace();
  queue.replaceChildren();
  stage.replaceChildren();
  let group = "";
  data.players.forEach(player => {
    if (player.group !== group) {
      group = player.group;
      queue.append(el("div", {class: "group"}, group));
    }
    const button = el("button", {class: "item", type: "button"});
    button.append(el("strong", null, player.id));
    button.append(el("span", {class: "tag " + player.decision}, stampWord(player.decision) + (player.reports ? " · " + player.reports : "")));
    button.addEventListener("click", () => {
      const card = document.getElementById("card-" + player.id);
      if (card) card.scrollIntoView({block: "start"});
      queue.querySelectorAll("button").forEach(node => node.removeAttribute("aria-current"));
      button.setAttribute("aria-current", "true");
    });
    queue.append(button);
  });
  TAPES.forEach(tape => {
    const section = el("section", {class: "tape", id: tape.id});
    section.append(el("p", {class: "kicker"}, tape.kicker));
    section.append(el("h2", null, tape.title));
    section.append(el("p", {class: "section-lede"}, tape.lede));
    const players = tape.ids.map(id => byId[id]).filter(Boolean);
    const grid = el("div", {class: "grid n" + Math.min(3, players.length)});
    const maxY = tape.share ? sharedSpeed(tape.ids) : null;
    players.forEach(player => grid.append(cardFor(player, maxY)));
    section.append(grid);
    stage.append(section);
  });
  const limits = data.players[0] ? data.players[0].limits : "";
  stage.append(el("p", {class: "limits"}, limits));
  stage.append(el("p", {class: "limits", id: "still"}, "What still gets through. Aim that gets quiet only while the server says this client could not have known is a review. Aim that stays on a private replay is a review, even when the public charts are ordinary. A short crossing of that path stays clean. Aim that matches the wire snapshot, ahead of the picture the client draws, is a review. The same hits on that picture stay clean. Standing still is not a signal. A leftover that matches another customer is a watch, and it moves them up the scan. A teammate who swings faster than a voice is a watch. A wallhack that never aims at a hidden mover, never stays on the replay, aims at the picture rather than the wire, and does not get quieter when a mover exists stays clean. An assist that matches human lag, human noise, and a noise sequence of its own stays clean. A DMA read of the frame the game actually drew, a capture card looking at pixels, and a cheat that reimplements the official interpolator and waits out the delay, never have to lock the snapshot. A server that scores the wrong timeline frames a legal player. A listen server can forge every field. This does not end cheating. The seal is the hash of the decision and the reasons. A person still reviews."));
}
// j and k only. The arrow keys keep scrolling the page.
document.addEventListener("keydown", event => {
  if (event.key !== "j" && event.key !== "k") return;
  if (document.getElementById("key").hidden) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const tag = event.target && event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  event.preventDefault();
  const here = tapeHere();
  const next = event.key === "j" ? Math.min(tapeIds.length - 1, here + 1) : Math.max(0, here - 1);
  const node = document.getElementById(tapeIds[next]);
  if (node) node.scrollIntoView({block: "start"});
});
paint();
function fitChartText() {
  stage.querySelectorAll("svg").forEach(svg => {
    const width = svg.getBoundingClientRect().width;
    if (width) svg.style.setProperty("--chart-scale", Math.min(1.8, 720 / width).toFixed(3));
  });
}
fitChartText();
let fitting = 0;
window.addEventListener("resize", () => {
  cancelAnimationFrame(fitting);
  fitting = requestAnimationFrame(fitChartText);
});

// Two views on one page. A link into the answer key (#tape-…, #card-…, #still, #key)
// opens it; anything else opens the operations view.
function viewFor(hash) {
  return /^#(tape-|card-|still|key$)/.test(hash) ? "key" : "ops";
}
function showView(view) {
  const key = document.getElementById("key");
  const ops = document.getElementById("ops");
  key.hidden = view !== "key";
  ops.hidden = view !== "ops";
  document.getElementById("tab-key").setAttribute("aria-selected", String(view === "key"));
  document.getElementById("tab-ops").setAttribute("aria-selected", String(view === "ops"));
  document.body.classList.toggle("view-ops", view === "ops");
  if (view === "key") fitChartText();
  else if (window.fpsdetOpsRender) window.fpsdetOpsRender();
}
function route() {
  const hash = location.hash;
  const view = viewFor(hash);
  showView(view);
  if (view === "key" && hash.length > 1 && hash !== "#key") {
    // After the tab is visible and laid out, or the browser lands above the card.
    requestAnimationFrame(() => {
      const node = document.getElementById(hash.slice(1));
      if (node) node.scrollIntoView({block: "start"});
    });
  } else if (hash === "#key" || hash === "#ops") {
    requestAnimationFrame(() => window.scrollTo(0, 0));
  }
}
window.addEventListener("hashchange", route);
route();
window.addEventListener("load", () => { if (viewFor(location.hash) === "key") route(); });
</script>
</body>
</html>
"""
