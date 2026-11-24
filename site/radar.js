// The hero radar. One dot per watched project, drawn from the same feed the
// page already loads: nothing here is decoration only. A dot sits nearer the
// centre the more recently a first-time change was observed on that
// project's site; quiet projects sit on the outer ring; a hollow red dot is a
// site that could not be loaded. Pointing at a dot names it, and picking it
// narrows the feed to that project.
const TAU = Math.PI * 2;
const HOUR = 3600 * 1000;
const COLOURS = {hot: "#ff9457", warm: "#c98a66", quiet: "#8d96a3", down: "#ff8a85", ink: "#f2f1ec", line: "rgba(174,182,193,.20)"};

function ago(hours) {
  if (hours < 1) return Math.max(1, Math.round(hours * 60)) + "m ago";
  if (hours < 48) return Math.round(hours) + "h ago";
  return Math.round(hours / 24) + "d ago";
}

export function createRadar(canvas, readout, onPick) {
  const context = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let blips = [];
  let summary = "Scanning the watchlist…";
  let pointed = null;
  let selected = null;
  let size = 0;
  let onScreen = true;
  let frame = 0;
  const started = performance.now();

  function resize() {
    const box = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    size = box.width;
    canvas.width = Math.round(box.width * ratio);
    canvas.height = Math.round(box.width * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    paint();
  }

  function place(blip) {
    const reach = size / 2 - 22;
    return {x: size / 2 + Math.cos(blip.angle) * blip.ring * reach, y: size / 2 + Math.sin(blip.angle) * blip.ring * reach};
  }

  function paint() {
    if (!size) return;
    const centre = size / 2;
    const reach = centre - 22;
    const sweep = still ? -Math.PI / 3 : ((performance.now() - started) / 7000 % 1) * TAU;
    context.clearRect(0, 0, size, size);

    // The swept sector: a fading trail behind the leading edge.
    if (typeof context.createConicGradient === "function") {
      const trail = context.createConicGradient(sweep - 1.15, centre, centre);
      trail.addColorStop(0, "rgba(255,148,87,0)");
      trail.addColorStop(1.15 / TAU, "rgba(255,148,87,.26)");
      trail.addColorStop(Math.min(1, 1.15 / TAU + 0.001), "rgba(255,148,87,0)");
      context.fillStyle = trail;
      context.beginPath();
      context.arc(centre, centre, reach, 0, TAU);
      context.fill();
    }
    context.strokeStyle = COLOURS.line;
    context.lineWidth = 1;
    for (const ring of [0.25, 0.5, 0.75, 1]) {
      context.beginPath();
      context.arc(centre, centre, reach * ring, 0, TAU);
      context.stroke();
    }
    context.beginPath();
    context.moveTo(centre - reach, centre);
    context.lineTo(centre + reach, centre);
    context.moveTo(centre, centre - reach);
    context.lineTo(centre, centre + reach);
    context.stroke();
    context.strokeStyle = "rgba(255,148,87,.75)";
    context.lineWidth = 1.5;
    context.beginPath();
    context.moveTo(centre, centre);
    context.lineTo(centre + Math.cos(sweep) * reach, centre + Math.sin(sweep) * reach);
    context.stroke();

    // Ring legend, along the upper vertical.
    context.font = "9px 'Courier New', monospace";
    context.textAlign = "left";
    context.textBaseline = "bottom";
    context.fillStyle = "rgba(174,182,193,.62)";
    for (const [ring, text] of [[0.25, "1H"], [0.5, "24H"], [0.75, "3D"], [1, "QUIET"]]) context.fillText(text, centre + 4, centre - reach * ring - 2);

    // The mark, at the centre: three rising bars.
    const unit = Math.max(3, reach * 0.028);
    context.fillStyle = COLOURS.hot;
    [1, 2, 3].forEach((height, index) => context.fillRect(centre - unit * 2.5 + index * unit * 2, centre + unit * 1.5 - height * unit, unit, height * unit));

    for (const blip of blips) {
      const {x, y} = place(blip);
      // A dot brightens as the sweep passes it, then settles.
      let behind = (sweep - blip.angle) % TAU;
      if (behind < 0) behind += TAU;
      const flare = still ? 0 : Math.max(0, 1 - behind / 1.4);
      const chosen = blip === pointed || blip.mint === selected;
      const radius = (blip.state === "hot" ? 5.5 : 4.5) + flare * 2.5 + (chosen ? 2 : 0);
      const colour = COLOURS[blip.state];
      context.save();
      if (blip.state === "hot" || flare > 0.2 || chosen) {
        context.shadowColor = colour;
        context.shadowBlur = 10 + flare * 14 + (chosen ? 8 : 0);
      }
      context.globalAlpha = blip.state === "quiet" ? 0.55 + flare * 0.45 : 1;
      context.beginPath();
      context.arc(x, y, radius, 0, TAU);
      if (blip.state === "down") {
        context.strokeStyle = colour;
        context.lineWidth = 2;
        context.stroke();
      } else {
        context.fillStyle = colour;
        context.fill();
      }
      context.restore();
      if (chosen) {
        context.strokeStyle = COLOURS.ink;
        context.lineWidth = 1.5;
        context.beginPath();
        context.arc(x, y, radius + 5, 0, TAU);
        context.stroke();
      }
      if (blip.state === "hot" || blip.state === "warm" || chosen) {
        const right = Math.cos(blip.angle) >= 0;
        context.font = (chosen ? "600 " : "") + "11px 'Courier New', monospace";
        context.textAlign = right ? "left" : "right";
        context.textBaseline = "middle";
        context.fillStyle = chosen ? COLOURS.ink : "rgba(242,241,236,.82)";
        const label = "$" + blip.symbol;
        const width = context.measureText(label).width;
        let labelX = x + (right ? radius + 7 : -radius - 7);
        labelX = right ? Math.min(labelX, size - width - 2) : Math.max(labelX, width + 2);
        context.fillText(label, labelX, y);
      }
    }
  }

  function loop() {
    frame = 0;
    if (still || document.hidden || !onScreen) return;
    paint();
    frame = requestAnimationFrame(loop);
  }

  function wake() {
    if (!frame && !still) frame = requestAnimationFrame(loop);
    if (still) paint();
  }

  function nearest(event) {
    const box = canvas.getBoundingClientRect();
    const px = event.clientX - box.left;
    const py = event.clientY - box.top;
    let best = null;
    let bestDistance = Math.max(20, size * 0.06);
    for (const blip of blips) {
      const {x, y} = place(blip);
      const distance = Math.hypot(x - px, y - py);
      if (distance < bestDistance) {
        best = blip;
        bestDistance = distance;
      }
    }
    return best;
  }

  function say() {
    // Symbols and names come from token listings, so they are only ever set as text.
    readout.textContent = pointed ? `$${pointed.symbol} · ${pointed.note} · open its timeline` : summary;
  }

  canvas.addEventListener("pointermove", event => {
    const blip = nearest(event);
    if (blip === pointed) return;
    pointed = blip;
    canvas.style.cursor = blip ? "pointer" : "default";
    say();
    if (still) paint();
  });
  canvas.addEventListener("pointerleave", () => {
    pointed = null;
    canvas.style.cursor = "default";
    say();
    if (still) paint();
  });
  canvas.addEventListener("click", event => {
    const blip = nearest(event);
    if (blip) onPick(blip.mint);
  });

  if (typeof ResizeObserver === "function") new ResizeObserver(resize).observe(canvas);
  else window.addEventListener("resize", resize);
  if (typeof IntersectionObserver === "function") {
    new IntersectionObserver(entries => {
      onScreen = entries[0].isIntersecting;
      wake();
    }).observe(canvas);
  }
  document.addEventListener("visibilitychange", wake);
  resize();
  wake();

  return {
    // targets: the watchlist as the feed reports it.
    update(targets) {
      const now = Date.now();
      const ordered = [...targets].sort((left, right) => left.mint < right.mint ? -1 : 1);
      blips = ordered.map((target, index) => {
        const angle = (index / Math.max(1, ordered.length)) * TAU - Math.PI / 2 + 0.19;
        if (target.status === "unavailable") return {mint: target.mint, symbol: target.symbol, angle, ring: 1, state: "down", note: "could not be loaded"};
        if (!target.latest_signal) return {mint: target.mint, symbol: target.symbol, angle, ring: 1, state: "quiet", note: "no change seen since first check"};
        const hours = Math.max(0, (now - new Date(target.latest_signal.observed).getTime()) / HOUR);
        // 1 hour sits on the first ring, a day on the second, three days on the third.
        const ring = hours <= 1 ? 0.12 + 0.13 * hours : hours <= 24 ? 0.25 + 0.25 * Math.log(hours) / Math.log(24) : Math.min(0.9, 0.5 + 0.25 * Math.log(hours / 24) / Math.log(3));
        return {mint: target.mint, symbol: target.symbol, angle, ring, state: hours <= 24 ? "hot" : hours <= 72 ? "warm" : "quiet", note: "changed " + ago(hours)};
      });
      const hot = blips.filter(blip => blip.state === "hot").length;
      const down = blips.filter(blip => blip.state === "down").length;
      summary = blips.length ? `${blips.length} projects on the radar · ${hot ? hot + " changed in the last 24 hours" : "none changed in the last 24 hours"}` +
        `${down ? " · " + down + " unreachable" : ""} · pick a dot` : "The watchlist is empty.";
      canvas.dataset.blips = String(blips.length);
      canvas.dataset.hot = String(hot);
      if (pointed) pointed = blips.find(blip => blip.mint === pointed.mint) || null;
      say();
      if (still) paint();
    },
    select(mint) {
      selected = mint;
      if (still) paint();
    },
  };
}
