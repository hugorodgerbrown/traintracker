// Copy buttons. They start hidden, so without JavaScript the page shows the
// URL as text to select, and no button that does nothing.
for (const button of document.querySelectorAll("button[data-copy]")) {
  const source = document.querySelector(button.dataset.copy);
  const status = button.parentElement.querySelector("[role=status]");
  if (!source || !navigator.clipboard) continue;
  button.hidden = false;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(source.textContent.trim());
      status.textContent = "Copied.";
    } catch {
      status.textContent = "Couldn't copy. Select the address and copy it by hand.";
    }
  });
}

// Departure boards: the app that show_board draws in a chat (ui/board.html).
// The trains are a table in the page. Without JavaScript the table shows in
// the board's colours; here it is left to screen readers and each letter
// becomes a flap that turns to it. A board with data-feed (the shareable
// boards, site/boards.py) fetches its trains again every data-refresh seconds
// while the page is visible, and the flaps turn from the old letters to the new.
const FLAPS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-&'/()"; // the order the flaps turn in
const MAX_TURNS = 8;
const TICK_MS = 45;
const NARROW_BELOW = 480; // px of panel width
const STALE_AFTER_MS = 5 * 60 * 1000; // a live board this long without an update says so
const FETCH_TIMEOUT_MS = 20 * 1000;
const MIN_PITCH = 4; // px: a TV board's flaps are never smaller than this
const IDLE_MS = 3000; // on a TV, the cursor hides after this long without moving
const RELOAD_HOUR = 3; // a TV board reloads itself once a night, in this hour (UK time)

// Each layout is the lines of one train. A field is [key, width]; a number is
// that many columns of gap. Every line of a layout has the same number of columns.
const WIDE = [[["time", 5], 1, ["place", 18], 1, ["platform", 3], 1, ["expected", 9]]];
const NARROW = [
  [["time", 5], 1, ["place", 18]],
  [6, ["expected", 9], 1, ["plat", 8]],
];

/** How many columns one line of a layout takes. */
function columns(parts) {
  return parts.reduce((n, part) => n + (Array.isArray(part) ? part[1] : part), 0);
}

/** `layout` with `extra` more columns for the destination. On a second line,
 * they go in the gap before the platform, which stays at the end. */
function widen(layout, extra) {
  const [first, ...rest] = layout;
  return [
    first.map((part) => (Array.isArray(part) && part[0] === "place" ? [part[0], part[1] + extra] : part)),
    ...rest.map((parts) => [
      ...parts.slice(0, -2),
      parts[parts.length - 2] + extra,
      parts[parts.length - 1],
    ]),
  ];
}

const still = window.matchMedia("(prefers-reduced-motion: reduce)");
// The boards' clocks show the time in the UK, as the station's own would.
const UK_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/London",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

for (const figure of document.querySelectorAll(".departures")) board(figure);

function board(figure) {
  const panel = figure.querySelector(".panel");
  const table = figure.querySelector("table");
  const [time, place, platform, expected] = Array.from(
    table.tHead.rows[0].cells,
    (cell) => cell.textContent,
  );
  const headings = { time, place, platform, expected };
  let trains = Array.from(table.tBodies[0].rows, (row) => {
    const [time, place, platform, expected] = Array.from(row.cells, (cell) =>
      cell.textContent.trim(),
    );
    return { time, place, platform, expected };
  });
  const turning = new Map(); // flap -> the letters it has yet to show, and the ticks until it starts
  let timer = 0;
  let cells = []; // the flaps, in reading order
  let built = ""; // what `cells` was laid out for: the layout and the number of trains
  let laidOut = WIDE;

  const flaps = document.createElement("div");
  flaps.className = "flaps";
  flaps.setAttribute("aria-hidden", "true");
  table.parentElement.before(flaps);
  figure.classList.add("drawn");

  /** One line of the board: `make` adds what each field of `parts` shows. */
  function line(parts, className, make) {
    const element = document.createElement("div");
    element.className = className;
    let column = 1;
    for (const part of parts) {
      if (Array.isArray(part)) make(element, part[0], part[1], column);
      column += Array.isArray(part) ? part[1] : part;
    }
    return element;
  }

  /** Blank flaps for the trains, laid out for the panel's width. */
  function build(layout) {
    turning.clear();
    cells = [];
    flaps.replaceChildren();
    flaps.style.setProperty("--cols", columns(layout[0]));
    // An empty board is only its message, without headings over nothing.
    flaps.hidden = !trains.length;
    // Headings over one line a train; two lines a train don't line up under them.
    if (layout.length === 1) {
      flaps.append(
        line(layout[0], "line heads", (heads, key, width, column) => {
          const label = document.createElement("span");
          label.textContent = headings[key];
          label.style.gridColumn = `${column} / span ${width}`;
          heads.append(label);
        }),
      );
    }
    for (let n = 0; n < trains.length; n++) {
      const service = document.createElement("div");
      service.className = "service";
      for (const parts of layout) {
        service.append(
          line(parts, "line", (element, key, width, column) => {
            for (let k = 0; k < width; k++) {
              const cell = document.createElement("span");
              cell.className = "cell";
              cell.style.gridColumn = column + k;
              cell.now = " ";
              cells.push(cell);
              element.append(cell);
            }
          }),
        );
      }
      flaps.append(service);
    }
  }

  /** Turn every flap to the trains' letters, laying the board out first if needed. */
  function draw() {
    const layout = onTv() ? tvLayout() : panel.clientWidth < NARROW_BELOW ? NARROW : WIDE;
    const key = `${layout.length}:${columns(layout[0])}:${trains.length}`;
    if (key !== built) build(layout);
    built = key;
    laidOut = layout;
    fit();
    let i = 0;
    trains.forEach((train, row) => {
      const text = { ...train, plat: train.platform && `Plat ${train.platform}` };
      for (const parts of layout) {
        let column = 0;
        for (const part of parts) {
          if (!Array.isArray(part)) {
            column += part;
            continue;
          }
          const [key, width] = part;
          for (const character of text[key].toUpperCase().slice(0, width).padEnd(width)) {
            turn(cells[i++], character, row * 2 + (column++ >> 2));
          }
        }
      }
    });
  }

  /** On a TV, size the flaps so that every train fits the screen's width and
   * height, as large as they can be. Elsewhere the stylesheet sizes them from
   * the width alone. */
  function fit() {
    if (!onTv() || !trains.length) {
      flaps.style.removeProperty("--pitch");
      return;
    }
    flaps.style.setProperty("--pitch", `${drawnPitch(laidOut)}px`);
  }

  /** The layout with the larger flaps on this screen, one line a train on a
   * wide screen and two on a tall one, with the destination widened to fill
   * the columns the screen has to spare at that size. */
  function tvLayout() {
    const base = pitchFor(NARROW) > pitchFor(WIDE) ? NARROW : WIDE;
    // At the size the flaps will be drawn, which is never below MIN_PITCH.
    const pitch = drawnPitch(base);
    const fits = Math.floor(inner().across / (pitch + 2)); // flaps across, with their margins
    const extra = fits - columns(base[0]);
    return extra > 0 ? widen(base, extra) : base;
  }

  /** The pitch the flaps are drawn at in `layout`: the largest that fits,
   * to a tenth of a pixel, and no less than MIN_PITCH. */
  function drawnPitch(layout) {
    return Math.max(MIN_PITCH, Math.floor(pitchFor(layout) * 10) / 10);
  }

  /** The room inside the flaps' box, in px. */
  function inner() {
    const style = getComputedStyle(flaps);
    return {
      across: flaps.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
      down: flaps.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
    };
  }

  /** The largest pitch at which every train fits the flaps' box in `layout`. */
  function pitchFor(layout) {
    const { across, down } = inner();
    const cols = columns(layout[0]);
    // In units of the pitch (see site.css): a line of flaps is 1.6 high, with
    // 2px of margin; trains are 0.45 apart; the headings take 0.6 and 2px.
    const count = Math.max(1, trains.length);
    const lines = count * layout.length;
    const heads = layout.length === 1 ? 1 : 0;
    const units = lines * 1.6 + (count - 1) * 0.45 + heads * 0.6;
    const fixed = lines * 2 + heads * 2;
    // Each flap has 1px of margin on either side.
    return Math.min(across / cols - 2, (down - fixed) / units);
  }

  /** Turn one flap to `character`, starting after `wait` ticks. */
  function turn(cell, character, wait) {
    const queued = turning.get(cell);
    if (character === (queued ? queued.queue[queued.queue.length - 1] : cell.now)) return;
    const from = FLAPS.indexOf(cell.now);
    const to = FLAPS.indexOf(character);
    if (still.matches || from < 0 || to < 0) {
      turning.delete(cell);
      cell.now = cell.textContent = character;
      return;
    }
    // Flaps only turn forwards. A long way round is cut to its last few turns.
    const turns = (to - from + FLAPS.length) % FLAPS.length;
    const queue = [];
    for (let n = Math.max(1, turns - MAX_TURNS + 1); n <= turns; n++) {
      queue.push(FLAPS[(from + n) % FLAPS.length]);
    }
    turning.set(cell, { queue, wait });
    if (!timer) timer = window.setInterval(tick, TICK_MS);
  }

  function tick() {
    for (const [cell, flap] of turning) {
      if (flap.wait-- > 0) continue;
      cell.now = cell.textContent = flap.queue.shift();
      cell.animate(
        [{ transform: "perspective(12em) rotateX(-75deg)", filter: "brightness(0.6)" }, {}],
        { duration: TICK_MS, easing: "ease-out" },
      );
      if (!flap.queue.length) turning.delete(cell);
    }
    if (!turning.size) {
      window.clearInterval(timer);
      timer = 0;
    }
  }

  // A board laid out for the other width is drawn again at this one. On a TV,
  // the space left for the flaps changes with the notes as well.
  const resized = new ResizeObserver(draw);
  resized.observe(panel);
  resized.observe(flaps);
  draw();

  const clock = figure.querySelector(".clock");
  if (clock) tickClock(clock);
  if (figure.dataset.feed) {
    follow(figure, Number(figure.dataset.refresh) || 60, (data) => {
      trains = data.trains;
      draw();
    });
  }
}

// ------------------------------------------------------------ live boards

/** Show the time in the UK, as the station's own clock would. */
function tickClock(clock) {
  const show = () => {
    clock.textContent = UK_TIME.format(new Date());
  };
  show();
  window.setInterval(show, 1000);
}

/** Fetch the board every `seconds` while the page is visible, and hand each answer to `show`. */
function follow(figure, seconds, show) {
  const table = figure.querySelector("table");
  const empty = figure.querySelector(".empty");
  const notes = figure.querySelector(".notes");
  const credit = figure.querySelector(".credit");
  const updated = figure.querySelector(".updated");
  const status = figure.querySelector(".status");
  const stale = figure.querySelector(".stale-note");
  const next = figure.querySelector(".next");
  let last = Date.now();
  let good = Date.now(); // when the board last updated
  let goodAt = updated.textContent.replace("Updated ", "");
  let busy = false;

  async function update() {
    if (busy || document.hidden) return;
    busy = true;
    last = Date.now();
    try {
      const response = await fetch(figure.dataset.feed, {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout?.(FETCH_TIMEOUT_MS),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The board couldn't be fetched.");
      rows(table, data.trains);
      empty.hidden = data.trains.length > 0;
      empty.textContent = empty.dataset.empty; // in place of why it couldn't be fetched
      notes.replaceChildren(
        ...data.messages.map((message) => {
          const item = document.createElement("li");
          item.textContent = message;
          return item;
        }),
      );
      sourceCredit(credit, data.source);
      updated.textContent = `Updated ${data.updated}`;
      status.textContent = "";
      good = Date.now();
      goodAt = data.updated;
      show(data);
    } catch (error) {
      status.textContent = ` · Couldn't update: ${error.message}`;
    } finally {
      busy = false;
      // Old times on a board nobody is watching closely look as good as new ones.
      // A board that never had times ("-") says why in place of them instead.
      const old = goodAt !== "-" && Date.now() - good >= STALE_AFTER_MS;
      figure.classList.toggle("stale", old);
      stale.hidden = !old;
      stale.textContent = old ? `Not updated since ${goodAt}. These times may be out of date.` : "";
    }
  }

  // Each second: count down to the next fetch beside "Updated", and make it
  // when it is due.
  let due = Date.now() + seconds * 1000;
  async function countdown() {
    const left = Math.ceil((due - Date.now()) / 1000);
    if (left <= 0) {
      due = Date.now() + seconds * 1000;
      next.textContent = " · Updating…";
      await update();
    }
    if (!busy) {
      next.textContent = ` · Next update in ${Math.max(1, Math.ceil((due - Date.now()) / 1000))}s`;
    }
  }
  countdown();
  window.setInterval(countdown, 1000);
  // A page that comes back into view after a while catches up at once.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Date.now() - last >= seconds * 1000) due = 0;
  });
}

/** Put the trains in the table that screen readers read. */
function rows(table, trains) {
  const labels = [null, "Destination", "Platform", "Expected"];
  table.tBodies[0].replaceChildren(
    ...trains.map((train) => {
      const row = document.createElement("tr");
      ["time", "place", "platform", "expected"].forEach((key, n) => {
        const cell = row.insertCell();
        if (labels[n]) cell.dataset.label = labels[n];
        cell.textContent = train[key];
      });
      return row;
    }),
  );
}

function sourceCredit(credit, source) {
  if (source !== "darwin") {
    credit.textContent = "Booked times: Network Rail data feeds (OGL v3.0)";
    return;
  }
  if (credit.querySelector("a")) return;
  const link = document.createElement("a");
  link.href = "https://www.nationalrail.co.uk/";
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "Powered by National Rail Enquiries";
  credit.replaceChildren(link);
}

// ---------------------------------------------------------------- TV mode

// A board on a TV: ?tv=1, or the board page's "Show on TV" button, which
// switches the page to the same view in place and goes full screen; leaving
// full screen goes back. On a TV the board fills the screen (fit, above), the
// screen is kept awake, the cursor hides when the mouse is still, and the page
// reloads itself once a night, at a minute of its own so that every TV in the
// country doesn't ask at once.
const root = document.documentElement;
const loadedAt = Date.now();
const reloadMinute = 10 + Math.floor(Math.random() * 40);
let inPlace = false; // TV mode came from the button, not the address
let wakeLock = null;
let idle = 0;
let nightly = 0;

// Called while the boards are first drawn, before `root` above is set.
function onTv() {
  return document.documentElement.classList.contains("tv");
}

function startTv() {
  stayAwake();
  nudge();
  if (!nightly) nightly = window.setInterval(reloadAtNight, 60 * 1000);
  showFullscreenButtons();
}

async function stayAwake() {
  if (!onTv() || document.hidden || wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => {
      wakeLock = null;
    });
  } catch {
    // No wake lock here (an older browser, or refused): the screen may sleep.
  }
}

/** The mouse moved: show the cursor, and hide it again when it stops. */
function nudge() {
  root.classList.remove("idle");
  window.clearTimeout(idle);
  if (onTv()) idle = window.setTimeout(() => root.classList.add("idle"), IDLE_MS);
}

function reloadAtNight() {
  if (!onTv()) return;
  const [hour, minute] = UK_TIME.format(new Date()).split(":").map(Number);
  if (hour === RELOAD_HOUR && minute === reloadMinute && Date.now() - loadedAt > 60 * 60 * 1000) {
    window.location.reload();
  }
}

function goFullscreen() {
  return root.requestFullscreen?.() ?? Promise.reject(new Error("No full screen here"));
}

/** Back to the page as it was, after TV mode from the button. */
function leaveTv() {
  inPlace = false;
  root.classList.remove("tv", "idle");
  window.history.replaceState(null, "", tvAddress(false));
  wakeLock?.release();
  showFullscreenButtons();
}

function showFullscreenButtons() {
  for (const button of document.querySelectorAll(".departures .fullscreen")) {
    button.hidden = !onTv() || Boolean(document.fullscreenElement) || !root.requestFullscreen;
  }
}

/** The page's address with ?tv=1 set, or taken out. */
function tvAddress(on) {
  const url = new URL(window.location.href);
  if (on) url.searchParams.set("tv", "1");
  else url.searchParams.delete("tv");
  return url.href;
}

// Leaving full screen is the only way back from the button's TV mode, so the
// button is offered only where full screen is, and a refusal undoes it.
for (const button of document.querySelectorAll("button[data-tv]")) {
  button.hidden = !root.requestFullscreen;
  button.addEventListener("click", () => {
    inPlace = true;
    root.classList.add("tv");
    window.history.replaceState(null, "", tvAddress(true));
    goFullscreen().then(startTv, leaveTv);
  });
}

for (const button of document.querySelectorAll(".departures .fullscreen")) {
  button.addEventListener("click", () => goFullscreen().catch(() => {}));
}

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && inPlace) leaveTv();
  showFullscreenButtons();
});

document.addEventListener("visibilitychange", stayAwake);
for (const event of ["mousemove", "mousedown", "keydown", "touchstart"]) {
  document.addEventListener(event, nudge, { passive: true });
}
document.addEventListener("keydown", (event) => {
  if (onTv() && event.key === "f" && !document.fullscreenElement) goFullscreen().catch(() => {});
});

if (onTv()) startTv();

// --------------------------------------------------------- station picker

// The picker's station field suggests stations as you type. A suggestion
// fills in "Name (CRS)", which the server reads as that station.
for (const input of document.querySelectorAll("input[data-suggest]")) {
  const list = document.getElementById(input.getAttribute("list"));
  let asked = "";
  let wait = 0;
  input.addEventListener("input", () => {
    window.clearTimeout(wait);
    wait = window.setTimeout(async () => {
      const query = input.value.trim();
      if (query.length < 2 || query === asked || /\([A-Z]{3}\)$/.test(query)) return;
      asked = query;
      try {
        const response = await fetch(`${input.dataset.suggest}?q=${encodeURIComponent(query)}`);
        const found = await response.json();
        if (input.value.trim() !== query) return; // typed on since
        list.replaceChildren(
          ...found.map((station) => {
            const option = document.createElement("option");
            option.value = `${station.name} (${station.crs})`;
            return option;
          }),
        );
      } catch {
        // Suggestions are a help; the form works without them.
      }
    }, 150);
  });
}
