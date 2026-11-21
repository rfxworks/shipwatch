+++
id = "2026-11-21-verify-what-we-ship"
date = 2026-11-21

[[media]]
kind = "card"
kicker = "Open source"
headline = "Verify what we ship."
capture = "/verify.html"
viewport = "phone"
selector = "#site-files .verify-body"
wait_for = "#files-summary.ok"
alt = "Shipwatch card reading 'Verify what we ship', beside a screenshot of the verify page showing every site file matching its published hash."
+++
Shipwatch is now open source and every deploy is stamped with its commit.

The new verify page hashes the site's files in your browser and compares them with the public build manifest. It also shows the only official token status: none launched.

https://shipwatch.bwltd.kr/verify.html
