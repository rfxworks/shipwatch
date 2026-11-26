# Shipwatch

**Watch what the dev ships.** Shipwatch follows the websites behind Solana tokens and records what changes: page edits, script and stylesheet changes, linked GitHub releases, and outages. Each observation has a timestamp and evidence you can open.

- Site: <https://shipwatch.bwltd.kr>
- Check this build yourself: <https://shipwatch.bwltd.kr/verify.html> or [VERIFY.md](VERIFY.md)
- Token status: [TOKEN.md](TOKEN.md), the only official statement. It names the wallet that creates the token.

This repository is the complete source for everything Shipwatch serves. The site is built from it by GitHub Actions, and the server pulls its one Python file from that build.

## How it works

Paste a mint address. Shipwatch looks up the website listed for that token on DEX Screener, or named in the token's own on-chain metadata when DEX Screener lists none, and adds it to a shared public watchlist. Every five minutes the observer fetches the homepage and up to three scripts or stylesheets on the same host, and compares them with the previous check.

| You see | What it means |
| --- | --- |
| First observation | The baseline was recorded. |
| Page edit | The title or visible text changed. Up to two added and two removed lines are shown. |
| Code / style change | The bytes of a linked script or stylesheet changed. |
| Linked release | The one GitHub repository linked from the page published a new release. |
| Repository link | The linked repository changed. If a page links several, attribution is paused. |
| Check failed / Back online | Two checks in a row failed, or the site answered again. |
| Recurring | The same change was seen on this site before: rotating text, or scripts alternating between builds. The feed lists only its latest occurrence. |

The home page opens with a radar of the watchlist: one dot per project, nearer the centre the more recently its site changed. Picking a dot narrows the feed to that project. Below it the page is a sequence of blocks: the numbers, the token, adding a project, the latest changes and the watched projects as cards. Lists open short and grow on request.

An edit is a reason to look closer. It does not show that a feature works, that a contract was deployed, or that anything is backed by funds.

## What is counted

Shipwatch counts how often the site is opened, so we know whether anyone uses it. There is no cookie, no third-party script and no stored identifier: the browser keeps a flag saying it has been here before and tells the server only "new" or "returning". The server stores daily totals, public at <https://api.shipwatch.bwltd.kr/api/stats>. To stop one connection inflating them, a salted hash of the connection is tallied in memory until the day ends and is never written to storage. Automated browsers are not counted. Counting began on 24 November 2026; earlier visits were not recorded.

## What is in here

| Path | Purpose |
| --- | --- |
| `site/` | The website. Served byte for byte as it appears here. |
| `backend/shipwatch.py` | The observer and its API. One file, standard library only. |
| `backend/deployer.py` | Runs on the server. Pulls the observer from the public site, checks its hash, and rolls back if the new version is unhealthy. |
| `tools/` | The build, the browser checks and the live-release check. |
| `token.toml`, `TOKEN.md` | The only official statement of the token's status. |
| `site/token-plan.json` | The planned launch day and the creator wallet, published before the launch. |
| `site/about.html` | Who builds this: Reflex Works, and the rules it ships by. |
| `.github/workflows/` | Checks on pull requests; build, attestation and deploy on `main`. |

## How a change reaches the public

1. A commit lands on `main`.
2. GitHub Actions runs the tests, builds `dist/`, checks both pages in a real browser, and signs a build attestation for the file manifest.
3. The build is deployed to GitHub Pages. `build.json` on the site names the commit.
4. The server checks the site every two minutes. If the observer's hash changed, it installs the new file, restarts, and confirms the running hash. A failed health check restores the previous file.

No step needs a person, and no workflow in this repository holds a secret.

## Run it yourself

The observer reads only public inputs, so you can run the same code and compare your results with the public feed.

```sh
python -m unittest discover -s backend -p 'test_*.py'   # tests, Python 3.9+
SHIPWATCH_STATE=./state python backend/shipwatch.py      # API on 127.0.0.1:8789
```

To build the site (Python 3.11+; media rendering needs Playwright):

```sh
python tools/build.py --no-media
python -m http.server --directory dist 8000
```

## Limits

- The watchlist is shared and capped at 40 projects during the pilot.
- A token's website comes from its DEX Screener listing or, when that names none, from the metadata its creator published with the token. The metadata route covers Token-2022 mints that carry their own metadata, which is how Pump.fun creates coins. Either source is the token's own claim: a token can name a website that has nothing to do with it.
- Sites behind a browser challenge cannot be read and are reported as failed checks.
- The observer's reported hash is the server's own statement. [VERIFY.md](VERIFY.md) says what that does and does not prove.

## Contact

Feedback and reports: [@rfxworks](https://x.com/rfxworks) on X, or open an issue here.

Built by Reflex Works. Code under the [MIT License](LICENSE).
