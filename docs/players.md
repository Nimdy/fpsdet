# For players: what this server records, and how review works

This page is for players on a server that uses fpsdet. The operator (the studio or community that runs the server) links it here. fpsdet is software the operator runs itself. The data stays with the operator, apart from the optional AI summaries described below.

fpsdet looks for cheating in what the game server already knows. It writes a **case**: a short file of numbers that a person on the operator's team can open. It does not ban anyone.

## What is recorded

Only events from the **dedicated server**: the machine that runs the match and decides what counts as a hit. Your PC sends the server your movement and your shots, as it would for any online match. The server writes a line for:

- **Each shot it accepts.** The weapon and its attachments, whether it hit, where it hit (head, torso, limbs), the distance, the recoil the server applied, and your view movement on the same tick.
- **Movement, several times a second** (about ten is typical). Your speed, whether you were on the ground, the weight of your kit, the speed limit the server applied, and whether something like an explosion or a vehicle moved you.
- **Aim measurements the server computes.** How steady your aim was, whether the server says you could see or hear the enemy you aimed at, how long your aim stayed on something you could not see or hear, and how far your aim was from where your game was drawing that enemy.

Each line also has a match id, the time in the match, your rank band, the server's id for the enemy you aimed at, and, if you queued with a group, a party id.

**Your account name is not in these lines.** You appear under a pseudonym: a stable id that stands in for your account. The operator keeps the link from pseudonym to account on its own systems.

**Player reports** move an account to the front of the queue for checking. They do not count as evidence and do not change the result.

## What is not collected

fpsdet installs nothing on your PC. It has no kernel driver. It does not read your hardware id (HWID), your raw mouse input, your PC's memory, or your screen. If this server also runs other anti-cheat software, that is a separate product with its own notice.

## How long it is kept

The raw shot and movement lines are kept only as long as the operator needs them to rebuild a case. fpsdet's docs suggest 14 to 30 days for a live game. The operator states the actual period in its notice. Case files and summary statistics across many players are kept longer.

## Who decides

fpsdet never decides what happens to your account. Every case it writes has `automated_action` set to `none`. A case marked `review` means "a person should look at this." A case marked `watch` means "keep an eye on it." Any action on an account is a person's decision: a reviewer on the operator's team opens the case, and can watch the match replay, before anything happens.

This matters under laws such as Article 22 of the EU General Data Protection Regulation (GDPR), which limits decisions based solely on automated processing that significantly affect a person. Whether a particular operator's process meets that law is for that operator. This page is not legal advice.

If the operator turns on AI summaries, a language model receives the summary numbers of a case. By default, before it is sent, your pseudonym, and any other player's it mentions, are replaced with stand-in names, and the party id, match ids, and seal are removed. It does not receive the raw lines. It writes a short note for the reviewer. It cannot change the decision.

## The server may send things your game never draws

To catch cheats that read data your game never displays, the server may send bodies or replays of movement that an honest game client never draws: hidden behind walls, out of earshot, or otherwise not visible to you. If you play normally, you will never see them. The operator does not publish the details, because published details would let cheats avoid them.

## How to appeal

Contact the operator through the channel named in its notice, and ask for your case.

A case is the appeal packet. It contains:

- the decision (`review`, `watch`, `clean`, or `insufficient_data`) and the reasons in plain sentences;
- each number that was checked, the limit it was compared to, and the best real players it was compared with;
- the sample size: how many shots or movement samples were used;
- where the speed limit came from (the server, or the game's profile);
- how many samples were left out because something like an explosion or a vehicle moved you;
- the match ids, so the reviewer can find the replays;
- a statement that the case is not a ban;
- a **seal**: a fingerprint (SHA-256) of the player, the game, the decision, and the reasons. It lets you and the reviewer confirm you are looking at the same packet. If a finding changes, the seal changes.

Things that often explain a case, worth raising in an appeal: an explosion, ragdoll, or vehicle that moved you; a perk, stim, or stance that raised your speed limit; tracking an enemy you could hear; a teammate's callout; a well-practised spray pattern.

## For operators

Link this page, and publish a notice like the one below where players will find it, such as your rules page or the server browser description. Fill in every bracket. Keep it accurate: if you turn something on or off, update the notice.

The notice discloses decoys in general terms. Do not publish where or how they are placed. If an appeal rests on a decoy, you can describe it to the player in general terms without giving away placement.

> **Anti-cheat on [Game] servers**
>
> [Studio] runs fpsdet on its dedicated servers. It records the shots and movement our servers already process, under a pseudonym, and looks for play that breaks the game's own rules or goes past what the best human players have been measured doing. fpsdet installs nothing on your PC and does not collect hardware ids, raw mouse input, or your PC's memory. [If you also run client-side anti-cheat, name it here and link its notice.]
>
> fpsdet does not ban. A member of our team reviews every case before any action is taken.
>
> Our servers may send bodies or movement that an honest game client never draws. Normal play never sees them. We do not publish details.
>
> We keep raw match events for [N] days. Case files are kept for [period]. AI summaries are [off / on, with ids removed before sending to [provider]].
>
> To ask about or appeal a decision, contact [link or address]. We will send you the case for your account and its seal.
>
> More: [link to this page]
