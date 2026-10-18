// Tests for static/js/saved_boards.js: the last board and favourites, kept by
// the browser when signed out and by the account (/api/boards) when signed in.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/saved_boards.js';

const { SavedBoards } = self;
const LST = { path: '/board/LST', name: 'London Liverpool Street' };
const COL = { path: '/board/LST/COL', name: 'London Liverpool Street to Colchester' };

/** A fetch that answers each call with the next of `answers` ({status, body}),
 * or throws one that is an Error. */
function server(...answers) {
  const fetch = vi.fn(async () => {
    const answer = answers.shift();
    if (answer instanceof Error) throw answer;
    return {
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: async () => answer.body,
    };
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

const signedOut = { status: 200, body: { signed_in: false } };
function signedIn(last = null, favourites = []) {
  return { status: 200, body: { signed_in: true, last, favourites, csrf: 'token' } };
}

/** The body a fetch call posted, and its address. */
function sent(fetch, n) {
  const [url, options] = fetch.mock.calls[n];
  return { url, body: options.body && JSON.parse(options.body), headers: options.headers };
}

async function settle() {
  for (let n = 0; n < 10; n++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function boardPage(board = COL) {
  document.body.innerHTML = `
    <div class="favourite" data-board-path="${board.path}" data-board-name="${board.name}" hidden>
      <button type="button">Add to favourites</button>
      <p class="hint"></p>
      <p class="status" role="status"></p>
    </div>`;
  return document.querySelector('.favourite');
}

function frontPage() {
  document.body.innerHTML = `
    <section class="your-favourites" hidden><h2>Your favourites</h2><ul class="chips"></ul></section>`;
  return document.querySelector('.your-favourites');
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the browser keeps', () => {
  it('nothing at first, and nothing it cannot read', () => {
    expect(SavedBoards.read()).toEqual({ last: null, favourites: [] });
    localStorage.setItem(SavedBoards.KEY, '{not json');
    expect(SavedBoards.read()).toEqual({ last: null, favourites: [] });
    localStorage.setItem(
      SavedBoards.KEY,
      JSON.stringify({ last: { path: 'https://example.com/' }, favourites: [LST, 7, { path: 3 }] }),
    );
    expect(SavedBoards.read()).toEqual({ last: null, favourites: [LST] });
  });

  it('the newest twelve favourites, each once', () => {
    let state = { last: null, favourites: [] };
    for (let n = 1; n <= 13; n++) {
      state = SavedBoards.withFavourite(state, { path: `/board/LST/${n}`, name: `${n}` }, true);
    }
    expect(state.favourites).toHaveLength(SavedBoards.MAX_FAVOURITES);
    expect(state.favourites[0].path).toBe('/board/LST/2');
    state = SavedBoards.withFavourite(state, { path: '/board/LST/2', name: '2' }, true);
    expect(state.favourites.at(-1).path).toBe('/board/LST/2');
    expect(state.favourites).toHaveLength(SavedBoards.MAX_FAVOURITES);
    state = SavedBoards.withFavourite(state, { path: '/board/LST/2', name: '2' }, false);
    expect(state.favourites.map((b) => b.path)).not.toContain('/board/LST/2');
  });
});

describe('load', () => {
  it('signed out, reads the browser', async () => {
    SavedBoards.write({ last: LST, favourites: [COL] });
    server(signedOut);
    expect(await SavedBoards.load()).toEqual({ signedIn: false, last: LST, favourites: [COL] });
  });

  it('offline, reads the browser', async () => {
    SavedBoards.write({ last: LST, favourites: [] });
    server(new TypeError('Failed to fetch'));
    expect(await SavedBoards.load()).toEqual({ signedIn: false, last: LST, favourites: [] });
  });

  it('when the server fails, gives nothing', async () => {
    server({ status: 500, body: {} });
    expect(await SavedBoards.load()).toBeNull();
  });

  it('signed in, reads the account', async () => {
    const fetch = server(signedIn(COL, [LST]));
    expect(await SavedBoards.load()).toEqual({ signedIn: true, last: COL, favourites: [LST] });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('signed in, moves what the browser kept into the account, then forgets it', async () => {
    SavedBoards.write({ last: LST, favourites: [LST, COL] });
    const fetch = server(signedIn(), { status: 200, body: { last: LST, favourites: [LST, COL] } });
    expect(await SavedBoards.load()).toEqual({ signedIn: true, last: LST, favourites: [LST, COL] });
    const move = sent(fetch, 1);
    expect(move.url).toBe('/api/boards/move');
    expect(move.body).toEqual({ last: '/board/LST', favourites: ['/board/LST', '/board/LST/COL'] });
    expect(move.headers['X-CSRFToken']).toBe('token');
    expect(localStorage.getItem(SavedBoards.KEY)).toBeNull();
  });

  it('keeps the browser copy when the move fails, to try again', async () => {
    SavedBoards.write({ last: LST, favourites: [] });
    server(signedIn(), { status: 500, body: {} });
    expect(await SavedBoards.load()).toEqual({ signedIn: true, last: null, favourites: [] });
    expect(SavedBoards.read().last).toEqual(LST);
  });
});

describe('a board page', () => {
  it('signed out, is remembered in the browser and can be a favourite there', async () => {
    const box = boardPage();
    server(signedOut);
    SavedBoards.start(document);
    await settle();
    expect(box.hidden).toBe(false);
    expect(box.querySelector('.hint').textContent).toBe(
      'Keeps it on your front page, in this browser.',
    );
    expect(SavedBoards.read().last).toEqual(COL);
    const button = box.querySelector('button');
    button.click();
    await settle();
    expect(button.textContent).toBe('Remove from favourites');
    expect(button.classList.contains('quiet')).toBe(true);
    expect(box.querySelector('.status').textContent).toBe('Added to your favourites.');
    expect(SavedBoards.read().favourites).toEqual([COL]);
    button.click();
    await settle();
    expect(button.textContent).toBe('Add to favourites');
    expect(SavedBoards.read().favourites).toEqual([]);
  });

  it('signed in, is kept with the account, and the browser keeps nothing', async () => {
    const box = boardPage();
    const fetch = server(
      signedIn(LST, [COL]),
      { status: 200, body: { last: COL, favourites: [COL] } },
      { status: 200, body: { last: COL, favourites: [] } },
    );
    SavedBoards.start(document);
    await settle();
    expect(sent(fetch, 1)).toMatchObject({ url: '/api/boards/opened', body: { path: COL.path } });
    expect(box.querySelector('.hint').textContent).toBe(
      'Keeps it on your front page, on every device you sign in on.',
    );
    const button = box.querySelector('button');
    expect(button.textContent).toBe('Remove from favourites');
    button.click();
    await settle();
    expect(sent(fetch, 2)).toMatchObject({
      url: '/api/boards/favourite',
      body: { path: COL.path, favourite: false },
    });
    expect(button.textContent).toBe('Add to favourites');
    expect(localStorage.getItem(SavedBoards.KEY)).toBeNull();
  });

  it('says when a favourite could not be saved', async () => {
    const box = boardPage();
    server(signedIn(), { status: 200, body: { last: COL, favourites: [] } }, { status: 403, body: {} });
    SavedBoards.start(document);
    await settle();
    box.querySelector('button').click();
    await settle();
    const status = box.querySelector('.status');
    expect(status.textContent).toBe("Couldn't save that. Try again.");
    expect(status.classList.contains('error')).toBe(true);
    expect(box.querySelector('button').textContent).toBe('Add to favourites');
  });

  it('stays hidden when the server fails', async () => {
    const box = boardPage();
    server({ status: 500, body: {} });
    SavedBoards.start(document);
    await settle();
    expect(box.hidden).toBe(true);
  });
});

describe('the front page', () => {
  it('shows each favourite as a link to its board', async () => {
    const section = frontPage();
    SavedBoards.write({ last: LST, favourites: [LST, COL] });
    server(signedOut);
    const state = await SavedBoards.start(document);
    await settle();
    expect(state.last).toEqual(LST);
    expect(section.hidden).toBe(false);
    const links = [...section.querySelectorAll('a')].map((a) => [a.getAttribute('href'), a.textContent]);
    expect(links).toEqual([
      ['/board/LST', 'London Liverpool Street'],
      ['/board/LST/COL', 'London Liverpool Street to Colchester'],
    ]);
  });

  it('shows no favourites section without any', async () => {
    const section = frontPage();
    server(signedOut);
    await SavedBoards.start(document);
    await settle();
    expect(section.hidden).toBe(true);
  });

  it('asks nothing on a page without saved boards', async () => {
    const fetch = server();
    expect(await SavedBoards.start(document)).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
});
