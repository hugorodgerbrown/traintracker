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

// The homepage's departure board: the app that show_board draws in a chat
// (ui/board.html), with the example trains in the page. Without JavaScript
// they stay a table; here the table is left to screen readers and each letter
// becomes a flap that turns to it.
const FLAPS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:.-&'/()"; // the order the flaps turn in
const MAX_TURNS = 8;
const TICK_MS = 45;
const NARROW_BELOW = 480; // px of panel width

// Each layout is the lines of one train. A field is [key, width]; a number is
// that many columns of gap. Every line of a layout has the same number of columns.
const WIDE = [[["time", 5], 1, ["place", 18], 1, ["platform", 3], 1, ["expected", 9]]];
const NARROW = [
  [["time", 5], 1, ["place", 18]],
  [6, ["expected", 9], 1, ["plat", 8]],
];

for (const figure of document.querySelectorAll(".departures")) {
  const panel = figure.querySelector(".panel");
  const table = figure.querySelector("table");
  const [time, place, platform, expected] = Array.from(
    table.tHead.rows[0].cells,
    (cell) => cell.textContent,
  );
  const headings = { time, place, platform, expected };
  const trains = Array.from(table.tBodies[0].rows, (row) => {
    const [time, place, platform, expected] = Array.from(row.cells, (cell) =>
      cell.textContent.trim(),
    );
    return { time, place, platform, plat: platform && `Plat ${platform}`, expected };
  });
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  const turning = new Map(); // flap -> the letters it has yet to show, and the ticks until it starts
  let timer = 0;
  let laidOut = null;

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

  /** Lay the board out for the panel's width, if it isn't already. */
  function draw() {
    const layout = panel.clientWidth < NARROW_BELOW ? NARROW : WIDE;
    if (layout === laidOut) return;
    laidOut = layout;
    turning.clear();
    flaps.replaceChildren();
    flaps.style.setProperty(
      "--cols",
      layout[0].reduce((n, part) => n + (Array.isArray(part) ? part[1] : part), 0),
    );
    if (layout === WIDE) {
      flaps.append(
        line(layout[0], "line heads", (heads, key, width, column) => {
          const label = document.createElement("span");
          label.textContent = headings[key];
          label.style.gridColumn = `${column} / span ${width}`;
          heads.append(label);
        }),
      );
    }
    trains.forEach((train, row) => {
      const service = document.createElement("div");
      service.className = "service";
      for (const parts of layout) {
        service.append(
          line(parts, "line", (cells, key, width, column) => {
            for (const character of train[key].toUpperCase().slice(0, width).padEnd(width)) {
              const cell = document.createElement("span");
              cell.className = "cell";
              cell.style.gridColumn = column;
              cells.append(cell);
              turn(cell, character, row * 2 + (column++ >> 2));
            }
          }),
        );
      }
      flaps.append(service);
    });
  }

  /** Turn one blank flap to `character`, starting after `wait` ticks. */
  function turn(cell, character, wait) {
    const to = FLAPS.indexOf(character);
    if (to === 0) return;
    if (still.matches || to < 0) {
      cell.textContent = character;
      return;
    }
    // Flaps only turn forwards. A long way round is cut to its last few turns.
    const queue = [...FLAPS.slice(Math.max(1, to - MAX_TURNS + 1), to + 1)];
    turning.set(cell, { queue, wait });
    if (!timer) timer = window.setInterval(tick, TICK_MS);
  }

  function tick() {
    for (const [cell, flap] of turning) {
      if (flap.wait-- > 0) continue;
      cell.textContent = flap.queue.shift();
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

  // A board laid out for the other width is drawn again at this one.
  new ResizeObserver(draw).observe(panel);
  draw();
}
