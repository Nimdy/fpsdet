# Server-side culling

Culling stops the server from sending a client anything about an enemy that client cannot see or hear. It prevents wallhacks. fpsdet detects cheats. Run both: culling removes most of what a wallhack could show, and fpsdet scores the part that is left.

## What culling does

A wallhack, an ESP overlay, a radar and a DMA card all read the same thing: the enemy positions the server sent to that client. If the server never sends them, there is nothing to read.

```
   wall |  player
        |  -------
        |      wall

                 enemy (outside both walls)
```

The enemy above is behind two solid walls. A culling server checks line of sight from that player to the enemy every tick, finds none, and leaves the enemy out of that player's snapshot. The enemy is not in that client's memory or packets. X-ray, ESP and radar draw nothing, and so does a second PC reading the first one's memory.

Riot built this into VALORANT and calls it Fog of War: [Demolishing Wallhacks with VALORANT's Fog of War](https://technology.riotgames.com/node/108). Community server plugins do the same for other games.

## What it does not close

Culling makes a wallhack close to useless. It does not make it useless:

1. **The lead window.** An enemy has to arrive before they round the corner, or they pop in late and the peeker always wins. The server sends them early by about half the client's round trip, plus the interpolation delay, plus a margin, and it tests visibility against several points on the body rather than one. For that window, often 100 to 300 ms, a wallhack sees someone the player cannot. That is the window around corners, where wallhacks matter most.
2. **Sound.** 3D audio needs a position. If the server sends a footstep's exact position, a cheat draws it as a dot. Send the sound with a coarse or offset position, limit its range, or send the sound without the body.
3. **Teammates.** Each teammate's client receives the enemies that teammate can see. A shared cheat, a stream, or a voice callout passes that on.
4. **Everything that is not a wallhack.** Aimbots, triggerbots, no-recoil and speed hacks use data the client is allowed to have. Culling does nothing about them.
5. **Cost and caution.** Visibility tests for every player pair, every tick, are expensive. Use precomputed visibility (PVS) to skip most pairs, then a few raycasts for the rest. When unsure, send. A culling bug that hides a visible enemy breaks the game for honest players.

## How it changes the fpsdet fields

Culling shrinks the information a cheat can use to the lead window, the sound you send, and what teammates share. fpsdet's information checks score exactly that remainder.

- **`hidden_track_ms`:** count only enemies that were sent to this client but that it could not see or hear (the lead window). An enemy that was never sent is not in that client's memory. Aim on them is prediction, a pre-aim or a callout, so leave it out. Otherwise a good player's prefire becomes evidence.
- **The teammate check gets sharper.** A teammate who swings an enemy their own client never received, 40 ms after the cheater's client had them, is the shared-radar signature. A voice is slower than `voice_min_ms`.
- **The private replay gets stronger.** With culling on, an honest client never receives a body behind a wall. A body the server deliberately sends into a culled region is never drawn by the stock client. Only software that reads memory or packets reacts to it. Tell players, in general terms, that the server may do this; see [players.md](players.md).
- **Wire against picture** still applies to visible enemies. Culling does not change it.
- **`information_state`** is unchanged. A culled enemy is `unknowable` to that client. Send `since_perceived_ms` so a body that just broke line of sight is not scored.

## Sketch

Engine-agnostic. The visibility test is your engine's; the part that matters is the lead and the default.

```
every server tick, for each client C:
    lead = C.rtt / 2 + C.interp_delay + margin
    for each enemy E:
        if visible(C.eye, E.body_points, ahead=lead):
            replicate(E, to=C)                 # C may draw E
        elif E.made_sound and audible(E.sound, C.position):
            send_sound(E.sound, coarse(E.position), to=C)
        else:
            cull(E, from=C)                    # nothing about E reaches C
        emit fpsdet fields for C's shots with E as sent / not sent
```

`visible` should err toward true. When in doubt, send the enemy. Sending too much gives a wallhack a little more. Sending too little gets honest players shot by enemies their screen never showed.
