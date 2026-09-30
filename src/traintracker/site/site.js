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

// Each layout is the lines of one train. A field is [key, width]; a number is
// that many columns of gap. Every line of a layout has the same number of columns.
const WIDE = [[["time", 5], 1, ["place", 18], 1, ["platform", 3], 1, ["expected", 9]]];
const NARROW = [
  [["time", 5], 1, ["place", 18]],
  [6, ["expected", 9], 1, ["plat", 8]],
];

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
    flaps.style.setProperty(
      "--cols",
      layout[0].reduce((n, part) => n + (Array.isArray(part) ? part[1] : part), 0),
    );
    // An empty board is only its message, without headings over nothing.
    flaps.hidden = !trains.length;
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
    const layout = panel.clientWidth < NARROW_BELOW ? NARROW : WIDE;
    const key = `${layout === WIDE ? "wide" : "narrow"}:${trains.length}`;
    if (key !== built) build(layout);
    built = key;
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

  // A board laid out for the other width is drawn again at this one.
  new ResizeObserver(draw).observe(panel);
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
  let last = Date.now();
  let busy = false;

  async function update() {
    if (busy || document.hidden) return;
    busy = true;
    last = Date.now();
    try {
      const response = await fetch(figure.dataset.feed, { headers: { Accept: "application/json" } });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The board couldn't be fetched.");
      rows(table, data.trains);
      empty.hidden = data.trains.length > 0;
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
      show(data);
    } catch (error) {
      status.textContent = ` · Couldn't update: ${error.message}`;
    } finally {
      busy = false;
    }
  }

  window.setInterval(update, seconds * 1000);
  // A page that comes back into view after a while catches up at once.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Date.now() - last >= seconds * 1000) update();
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
