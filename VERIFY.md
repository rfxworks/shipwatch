# Verify Shipwatch

You can check that the site you are using was built from this repository, and that the observer reports running the published code. It takes about five minutes and needs only `curl`, `sha256sum` and, for the last step, the [GitHub CLI](https://cli.github.com).

The same checks run in your browser at <https://shipwatch.bwltd.kr/verify.html>. This page is for doing them without trusting that one.

## 1. Find the commit the site claims

```sh
curl -s https://shipwatch.bwltd.kr/build.json
```

`source.commit` is the commit the site says it was built from. Official builds also carry `source.run_url`, the GitHub Actions run that built it, and never show `"dirty": true`.

Set it for the next steps:

```sh
COMMIT=$(curl -s https://shipwatch.bwltd.kr/build.json | python3 -c "import json,sys; print(json.load(sys.stdin)['source']['commit'])")
```

## 2. Compare served files with the repository

Every file under `site/` is deployed byte for byte. Pick any of them:

```sh
curl -s https://shipwatch.bwltd.kr/app.js | sha256sum
curl -s https://raw.githubusercontent.com/rfxworks/shipwatch/$COMMIT/site/app.js | sha256sum
```

The two hashes must be equal. To check all of them at once, clone the repository and run:

```sh
git checkout $COMMIT
python3 tools/verify_live.py --commit $COMMIT
```

It downloads each file the manifest lists, hashes it, and fails on any difference.

## 3. Check that GitHub built these files from that commit

```sh
curl -sO https://shipwatch.bwltd.kr/SHA256SUMS
gh attestation verify SHA256SUMS --repo rfxworks/shipwatch
```

`SHA256SUMS` lists the hash of every deployed file, including `build.json`. The attestation is signed through Sigstore by the GitHub Actions run, so a passing check means this manifest came out of a workflow in this repository. Add `--source-digest $COMMIT` to pin it to the commit.

## 4. Check the observer

```sh
curl -s https://api.shipwatch.bwltd.kr/health
curl -s https://raw.githubusercontent.com/rfxworks/shipwatch/$COMMIT/backend/shipwatch.py | sha256sum
```

`code_sha256` from the first command should equal the hash from the second. Right after a release they can differ for a few minutes while the server installs the new file.


## What this proves, and what it does not

**Proven by steps 1 to 3.** At the moment you checked, the site served files identical to a specific public commit, and GitHub's build system produced that file list from that commit.

**Not proven.**

- *The observer hash is self-reported.* The server tells you which code it runs. Someone with control of the server could make it report any value. The observer reads only public inputs, so you can run `backend/shipwatch.py` yourself against the same mint and compare your observations with the public feed. That is the real check.
- *DNS is ours.* We control where `shipwatch.bwltd.kr` points. The checks above tell you what is served now, not what will be served later. Repeat them when it matters.
- *Open source is not a safety review.* These checks show where the code came from. They say nothing about whether a project on the watchlist is trustworthy.

## Token

The only official statement about a Shipwatch token is [TOKEN.md](TOKEN.md), mirrored at <https://shipwatch.bwltd.kr/verify.html#token>. Until a mint address appears in both, no Shipwatch token exists.
