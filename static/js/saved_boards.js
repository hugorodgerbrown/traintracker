/*
 * static/js/saved_boards.js — the last board you opened, and your favourites.
 *
 * Signed out, the browser keeps them (localStorage) and nothing is sent to
 * the server. Signed in, the account keeps them (/api/boards, saved.py) and
 * the browser keeps nothing, so they are the same on every device. Which one
 * applies is what /api/boards answers (`signed_in`). At sign-in,
 * what this browser kept is moved into the account once, then cleared, so
 * after signing out the browser is empty again.
 *
 * A board page (board.html) is remembered as the last board opened, and gets
 * its Add to favourites button. The front page shows the favourites as
 * chips; site.js draws the last board, live, from `SavedBoards.ready`.
 * Without this file, nothing is kept and the pages are as they were.
 */
(function () {
  'use strict';

  const KEY = 'traintrackr.boards';
  const API = '/api/boards';
  const MAX_FAVOURITES = 12; // as SavedBoard.MAX_FAVOURITES
  const HINT_BROWSER = 'Keeps it on your front page, in this browser.';
  const HINT_ACCOUNT = 'Keeps it on your front page, on every device you sign in on.';

  // ------------------------------------------------- the browser's copy

  function empty() {
    return { last: null, favourites: [] };
  }

  /** A board as kept: its address and short name, or null if it isn't one. */
  function clean(board) {
    if (!board || typeof board.path !== 'string' || typeof board.name !== 'string') return null;
    return board.path.startsWith('/board/') ? { path: board.path, name: board.name } : null;
  }

  /** What this browser keeps. Storage that is blocked or garbled reads as empty. */
  function read() {
    try {
      const kept = JSON.parse(localStorage.getItem(KEY));
      if (!kept) return empty();
      return {
        last: clean(kept.last),
        favourites: (Array.isArray(kept.favourites) ? kept.favourites : [])
          .map(clean)
          .filter(Boolean)
          .slice(-MAX_FAVOURITES),
      };
    } catch {
      return empty();
    }
  }

  function write(state) {
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      // Storage is full or blocked: the boards just aren't kept.
    }
  }

  function clear() {
    try {
      localStorage.removeItem(KEY);
    } catch {
      // Nothing to clear.
    }
  }

  function kept(state) {
    return Boolean(state.last || state.favourites.length);
  }

  /** `state` with `board` as the last board opened. */
  function withOpened(state, board) {
    return { ...state, last: board };
  }

  /** `state` with `board` added to the favourites (last) or taken out. The
   * oldest goes when there are too many. */
  function withFavourite(state, board, on) {
    const others = state.favourites.filter((b) => b.path !== board.path);
    return { ...state, favourites: (on ? [...others, board] : others).slice(-MAX_FAVOURITES) };
  }

  function isFavourite(state, path) {
    return state.favourites.some((b) => b.path === path);
  }

  // ------------------------------------------------------- the account

  let csrf = '';

  /** GET /api/boards, or POST `body` to API + `path`. */
  function call(path, body) {
    const headers = { Accept: 'application/json' };
    if (body) {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRFToken'] = csrf;
    }
    return fetch(API + path, {
      method: body ? 'POST' : 'GET',
      headers,
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  async function posted(path, body) {
    const response = await call(path, body);
    if (!response.ok) throw new Error(`Saving failed (${response.status}).`);
    return response.json();
  }

  // ---------------------------------------------------- both, as one

  /** Your boards, and where they're kept: `{signedIn, last, favourites}`.
   * Null when the account couldn't be asked (the server failed): then
   * nothing is shown or kept, rather than keeping a signed-in person's
   * boards in the browser. Offline reads as signed out. */
  async function load() {
    let response;
    try {
      response = await call('');
    } catch {
      return { signedIn: false, ...read() };
    }
    if (!response.ok) return null;
    const data = await response.json();
    if (!data.signed_in) return { signedIn: false, ...read() };
    csrf = data.csrf || '';
    let state = { last: data.last, favourites: data.favourites };
    const local = read();
    if (kept(local)) {
      try {
        state = await posted('/move', {
          last: local.last && local.last.path,
          favourites: local.favourites.map((b) => b.path),
        });
        clear();
      } catch {
        // Kept in the browser, to move next time.
      }
    }
    return { signedIn: true, last: state.last, favourites: state.favourites };
  }

  /** Remember `board` as the last opened; the boards as they now are. */
  async function opened(signedIn, board) {
    if (signedIn) return posted('/opened', { path: board.path });
    const state = withOpened(read(), board);
    write(state);
    return state;
  }

  /** Make `board` a favourite, or not; the boards as they now are. */
  async function favourite(signedIn, board, on) {
    if (signedIn) return posted('/favourite', { path: board.path, favourite: on });
    const state = withFavourite(read(), board, on);
    write(state);
    return state;
  }

  // ------------------------------------------------------ the pages

  /** A board page: remember it, and show its favourite button. */
  async function enhanceBoard(box, ready) {
    const board = { path: box.dataset.boardPath, name: box.dataset.boardName };
    const loaded = await ready;
    if (!loaded) return;
    const { signedIn } = loaded;
    let state = loaded;
    try {
      state = await opened(signedIn, board);
    } catch {
      // Not remembered this time; the button still works.
    }
    const button = box.querySelector('button');
    const status = box.querySelector('.status');
    box.querySelector('.hint').textContent = signedIn ? HINT_ACCOUNT : HINT_BROWSER;
    let on = isFavourite(state, board.path);
    const show = () => {
      button.textContent = on ? 'Remove from favourites' : 'Add to favourites';
      button.classList.toggle('quiet', on);
    };
    show();
    box.hidden = false;
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        on = isFavourite(await favourite(signedIn, board, !on), board.path);
        show();
        status.classList.remove('error');
        status.textContent = on ? 'Added to your favourites.' : 'Removed from your favourites.';
      } catch {
        status.classList.add('error');
        status.textContent = "Couldn't save that. Try again.";
      } finally {
        button.disabled = false;
      }
    });
  }

  /** The front page's favourites: a chip for each, linking to its board. */
  async function showFavourites(section, ready) {
    const state = await ready;
    if (!state || !state.favourites.length) return;
    section.querySelector('ul').replaceChildren(
      ...state.favourites.map((board) => {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = board.path;
        link.textContent = board.name;
        item.append(link);
        return item;
      }),
    );
    section.hidden = false;
  }

  /** Start on the pages that use saved boards; elsewhere ask nothing. */
  function start(root) {
    const box = root.querySelector('.favourite[data-board-path]');
    const section = root.querySelector('.your-favourites');
    if (!box && !section) return Promise.resolve(null);
    const ready = load();
    if (box) enhanceBoard(box, ready);
    if (section) showFavourites(section, ready);
    return ready;
  }

  self.SavedBoards = {
    KEY,
    MAX_FAVOURITES,
    read,
    write,
    clear,
    withOpened,
    withFavourite,
    load,
    opened,
    favourite,
    start,
    ready: start(document),
  };
})();
