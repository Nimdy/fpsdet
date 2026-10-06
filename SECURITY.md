# Security policy

fpsdet decides which players a person reviews. A flaw can let a cheater through, or put an honest player in front of a reviewer. Report those flaws privately.

## How to report

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and click **Report a vulnerability**, or go straight to <https://github.com/Nimdy/detect-FPS-hackers/security/advisories/new>. Only the maintainer can read the report.

If that button is missing, open a public issue that says only "I need a private security contact." Put no details in it.

## Report privately

- **A bypass.** Cheating play that stays under a check that should catch it.
- **A false case on purpose.** A way to make an innocent player come out as `review` or `watch`, for example through events, reports, party ids, or another account's data that the scorer trusts.
- **Injection.** HTML or script that runs in a case page (`cases/*.html`) or the review desk (`board.html`), for example from a player id, weapon id, map id, or AI brief.
- **A privacy leak.** A player id, party id, match id, or anything else that identifies a player reaching the AI endpoint, or any other place the docs say it does not go.
- **A challenge leak.** A server secret, a challenge's realization, or anything that lets a client predict a planned challenge, reaching a plan file, a case, the desk, the public pages or any other output ([docs/challenges.md](docs/challenges.md) lists where they must never appear).
- **A forged or misread signature.** An external record that verifies without its provider's registered key, or a revoked or unknown key that is read as verified ([docs/external-authentication.md](docs/external-authentication.md)).
- **Code from data.** An external record, an adapter or a profile that makes fpsdet run code, read a file it was not given, or reach the network.

Include the commit or version, the command, and the smallest input that shows the problem. Use made-up ids.

## Do not post in public

- Working cheat code.
- Step-by-step bypass instructions.
- Real player names, account ids, IP addresses, or match ids.

## Fine in a public issue

A false-positive report about honest play: a memorised spray, a sound cue, an untagged ragdoll, a perk the server did not send. Use the **False positive** issue form. Other operators learn from those.

If someone could trigger that false positive on purpose against another player, it is a false case on purpose. Report it privately.

## What to expect

There is no bug bounty and no fixed response time. Supported code is the latest commit on `main`. A fix that changes a decision ships with a planted test, so it stays fixed.
