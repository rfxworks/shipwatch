# Shipwatch token

**Status: launched on Pump.fun on 2026-11-26.**

| | |
| --- | --- |
| Mint address | `JD65LAUYrixxR4MWfXB9GgveP35LvR4cNqYXqqkNpump` |
| Symbol | `$SHIPWATCH` |
| Coin page | <https://pump.fun/coin/JD65LAUYrixxR4MWfXB9GgveP35LvR4cNqYXqqkNpump> |
| Creator wallet | [`FZWqyszTxQ2a3EsrAMkSvBac9A94erugDh7CTFR5D8zv`](https://solscan.io/account/FZWqyszTxQ2a3EsrAMkSvBac9A94erugDh7CTFR5D8zv) |
| Creation transaction | [`5QvDe76hEnVTwgy5kyRLZj6GQRga8MbiTqDVSczVBgyf46MM4KdV66uUQNe5CcQfGzhi8HugQMmTLYSBmTKzDQex`](https://solscan.io/tx/5QvDe76hEnVTwgy5kyRLZj6GQRga8MbiTqDVSczVBgyf46MM4KdV66uUQNe5CcQfGzhi8HugQMmTLYSBmTKzDQex) |
| Metadata | <https://ipfs.io/ipfs/bafkreiagd377acemlmjimhnkxmklyawa5ovx4b5a4nx4wrga67xnjurmxm> |

This is the only Shipwatch token. Check the full mint address, not only its first and last characters. The same address is shown at <https://shipwatch.bwltd.kr/verify.html#token>.

## What the team holds

The creator wallet bought **3,520,919 tokens (0.35% of supply)** for 0.1 SOL inside the creation transaction. That is the team's entire holding.

**Sell policy.** No sales in the first 30 days. Any later sale is announced in this repository before it happens.

**Creator fees.** The creator wallet. Creator fees pay for hosting and development.

No other wallet belongs to the team. Nothing was bundled across wallets at creation.

## Check it yourself

**The token was created by the wallet above.** Open the creation transaction. Its fee payer is the creator wallet, and the coin's bonding curve records that wallet as creator.

**The wallet names this repository on-chain.** [This transaction](https://solscan.io/tx/2RHPH4PDy79muZVRsmrGjzL6UntqdE8bgdHPpqZW3Tq7EhCA6SV3WxtfdwpT9dH5U7ti5GyLKUKPouWTNDUAhym3), signed by the creator wallet, carries a memo naming this site and repository.

**The supply cannot be changed.** Mint authority: `none`. Freeze authority: `none`. Supply: 1,000,000,000. The metadata address is fixed at creation.

**The metadata is exactly what is committed here.** [`token/metadata.json`](token/metadata.json) has SHA-256 `061efff0088c5b12861daabb14bc02c0ebab7e07a0e36fcb44c0f7eed4d22cbb` and [`token/image.png`](token/image.png) has SHA-256 `ad7aed0209161f562fbd71e5d310c055d4ab501c599a363769614ccd68bfa388`. Download the files from the metadata link and compare.

**The creator wallet signed a statement.** [`token/statement.txt`](token/statement.txt) is signed with the creator wallet's key; the signature is in [`token/statement.sig`](token/statement.sig). It is a plain Ed25519 signature over the file's bytes, which any Ed25519 library can check against the creator wallet's address. With Python and PyNaCl:

```python
import base64, base58, nacl.signing
key = nacl.signing.VerifyKey(base58.b58decode("FZWqyszTxQ2a3EsrAMkSvBac9A94erugDh7CTFR5D8zv"))
key.verify(open("token/statement.txt", "rb").read(), base64.b64decode(open("token/statement.sig").read()))
```

## What this token is

Shipwatch is a free tool. This token is attached to it and is not needed to use it. It is not an investment contract, it carries no claim on the project, and it may lose all of its value.
