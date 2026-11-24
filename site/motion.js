// Scroll effects shared by every page. All of it is optional: without this
// script, or for a visitor who asks for reduced motion, the page is simply
// shown as it is.
//
//   .reveal            fades and rises into place the first time it scrolls into view
//   [data-stat]        a number that counts up when its band is first seen (see setStat)
//   .header            gains .scrolled once the page has moved
//   nav a[data-spy]    is marked aria-current while its section is on screen
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const canWatch = typeof IntersectionObserver === "function";
document.documentElement.classList.add("js");

const seen = new WeakSet();
const pending = new Map();

function show(node) {
  node.classList.add("in");
  seen.add(node);
  for (const stat of node.querySelectorAll("[data-stat]")) {
    if (pending.has(stat)) count(stat, pending.get(stat));
    pending.delete(stat);
  }
}

if (still || !canWatch) {
  for (const node of document.querySelectorAll(".reveal")) show(node);
} else {
  const watcher = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      show(entry.target);
      watcher.unobserve(entry.target);
    }
  }, {rootMargin: "0px 0px -10% 0px", threshold: 0.06});
  for (const node of document.querySelectorAll(".reveal")) watcher.observe(node);
}

function count(node, value) {
  if (still) {
    node.textContent = value.toLocaleString("en-US");
    return;
  }
  const started = performance.now();
  const step = now => {
    const progress = Math.min(1, (now - started) / 900);
    node.textContent = Math.round(value * (1 - Math.pow(1 - progress, 3))).toLocaleString("en-US");
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// Set a figure in the numbers band. It counts up the first time the band is
// seen; later updates just replace the figure.
export function setStat(name, value) {
  const node = document.querySelector(`[data-stat="${name}"]`);
  if (!node || !Number.isFinite(value)) return;
  const band = node.closest(".reveal");
  if (node.dataset.shown) node.textContent = value.toLocaleString("en-US");
  else if (!band || seen.has(band)) count(node, value);
  else pending.set(node, value);
  node.dataset.shown = "1";
}

const header = document.querySelector(".header");
if (header) {
  const update = () => header.classList.toggle("scrolled", window.scrollY > 12);
  window.addEventListener("scroll", update, {passive: true});
  update();
}

if (canWatch) {
  const links = new Map([...document.querySelectorAll("nav a[data-spy]")].map(link => [link.dataset.spy, link]));
  const spy = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const link = links.get(entry.target.dataset.section);
      if (!link) continue;
      if (entry.isIntersecting) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    }
  }, {rootMargin: "-45% 0px -45% 0px"});
  for (const section of document.querySelectorAll("[data-section]")) spy.observe(section);
}
