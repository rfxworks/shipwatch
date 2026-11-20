# Posts

Every post Shipwatch publishes on X starts as a file in this folder. Merging a file into `main` is what publishes it, so an announcement from our account with no file here did not come from this pipeline.

Each file is one post, or a thread split by `---` lines. The header between the `+++` lines carries the post's id, the day it may go out, and its media. Images and videos are rendered from the working product during the build. A file marked `draft = true` is rendered for review and never published.

## What the build rejects

Run `python tools/posts.py` to check every post. The build fails when a post:

- promises a price, a multiple or a return, or pushes urgency to buy;
- links to a hosting provider's address instead of `shipwatch.bwltd.kr`;
- is longer than 280 weighted characters in any segment (a link counts as 23);
- has media without alt text.

## What happens after a merge

1. The release workflow builds the site, renders the media, and deploys `posts/queue.json`.
2. Our server reads that queue every two minutes and publishes at most one new post per run, within daily and monthly caps.
3. The post's link is added to <https://api.shipwatch.bwltd.kr/posts/receipts.json>.

A post that was published is never sent again, even if its file is edited later.
