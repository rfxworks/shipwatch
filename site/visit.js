// Count this opening of the site. No cookie and no identifier: the browser
// only says whether it has been here before, and the server keeps daily
// totals (public at /api/stats). Automated browsers are left out.
if (!navigator.webdriver) {
  let first = false;
  try {
    first = !localStorage.getItem("shipwatch-visited");
    localStorage.setItem("shipwatch-visited", "1");
  } catch { /* storage is unavailable: counted as a returning visit */ }
  fetch("https://api.shipwatch.bwltd.kr/api/visit", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({new: first}),
    keepalive: true,
  }).catch(() => {});
}
