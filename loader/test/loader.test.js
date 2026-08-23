import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { loadLoader, makeWindow } from './dom-stub.js';

const KEY = 'pk_live_abc123';
const BASE = 'https://widget.disputeshield.dev';

function mount(overrides = {}) {
  const win = makeWindow();
  const ds = loadLoader(win);
  const api = ds.init({ publishableKey: KEY, baseUrl: BASE, ...overrides });
  return { win, ds, api };
}

test('creates exactly one sandboxed cross-origin iframe', () => {
  const { win, api } = mount();
  assert.equal(win.document.body.children.length, 1);
  assert.equal(api.frame.tagName, 'IFRAME');
  assert.equal(
    api.frame.getAttribute('sandbox'),
    'allow-scripts allow-forms allow-same-origin'
  );
  assert.equal(api.frame.getAttribute('allow'), '');
  assert.equal(api.frame.getAttribute('referrerpolicy'), 'strict-origin');
});

test('the iframe src carries the publishable key and nothing secret', () => {
  const { api } = mount();
  assert.match(api.frame.src, /\/v1\/embed\?k=pk_live_abc123/);
  assert.ok(!/dst_/.test(api.frame.src), 'a session token must never reach the URL');
});

test('mounting twice does not create a second widget', () => {
  const { win, ds } = mount();
  const again = ds.init({ publishableKey: KEY, baseUrl: BASE });
  assert.equal(win.document.body.children.length, 1);
  assert.equal(win._listenerCount(), 1, 'a second listener would double-handle every message');
  assert.ok(again);
});

test('fails closed and silently without a publishable key', () => {
  const win = makeWindow();
  const ds = loadLoader(win);
  assert.equal(ds.init({}), null);
  assert.equal(win.document.body.children.length, 0, 'nothing may render on the host page');
});

test('never posts to a wildcard origin', () => {
  const { win, api } = mount();
  api.open();
  api.close();
  assert.ok(win._posted.length >= 2);
  for (const { targetOrigin } of win._posted) {
    assert.equal(targetOrigin, BASE, "'*' is how a widget integration leaks data");
  }
});

test('ignores a message from another origin', () => {
  const { win, api } = mount();
  win._deliver({
    origin: 'https://evil.example',
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'resize', width: 400, height: 600 } }
  });
  assert.equal(api.frame.style.width, '0', 'a foreign origin moved our frame');
});

test('ignores a message from the right origin but the wrong window', () => {
  const { win, api } = mount();
  win._deliver({
    origin: BASE,
    source: { not: 'our frame' },
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'resize', width: 400, height: 600 } }
  });
  assert.equal(api.frame.style.width, '0');
});

test('ignores a message with an unknown type', () => {
  const { win, api } = mount();
  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'exfiltrate' } }
  });
  assert.equal(api.frame.style.width, '0');
});

test('ignores a message with a mismatched protocol version', () => {
  const { win, api } = mount();
  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 99, payload: { type: 'resize', width: 400, height: 600 } }
  });
  assert.equal(api.frame.style.width, '0');
});

test('accepts a well-formed resize and bounds it', () => {
  const { win, api } = mount();
  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'resize', width: 400, height: 600 } }
  });
  assert.equal(api.frame.style.width, '400px');
  assert.equal(api.frame.style.height, '600px');
});

test('a hostile resize cannot cover the host page', () => {
  const { win, api } = mount();
  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: {
      source: 'disputeshield-widget',
      version: 1,
      payload: { type: 'resize', width: 99999, height: 99999 }
    }
  });
  assert.equal(api.frame.style.width, '480px');
  assert.equal(api.frame.style.height, '720px');
});

test('a non-numeric resize does not produce NaNpx', () => {
  const { win, api } = mount();
  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: {
      source: 'disputeshield-widget',
      version: 1,
      payload: { type: 'resize', width: 'tall', height: null }
    }
  });
  assert.equal(api.frame.style.width, '0px');
  assert.equal(api.frame.style.height, '0px');
});

test('a host callback that throws does not break the loader', () => {
  const win = makeWindow();
  const ds = loadLoader(win);
  const api = ds.init({
    publishableKey: KEY,
    baseUrl: BASE,
    onEvent() {
      throw new Error('the host has a bug');
    }
  });
  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'ready' } }
  });
  assert.ok(api.frame);
});

test('destroy removes the frame and the listener', () => {
  const { win, api } = mount();
  api.destroy();
  assert.equal(win.document.body.children.length, 0);
  assert.equal(win._listenerCount(), 0);
});

test('the loader reads nothing from the host page', () => {
  const source = readFileSync(new URL('../src/loader.js', import.meta.url), 'utf8');
  for (const forbidden of [
    'document.cookie',
    'localStorage',
    'sessionStorage',
    'querySelectorAll',
    'getElementsBy',
    'document.forms',
    'XMLHttpRequest',
    'fetch('
  ]) {
    assert.ok(
      !source.includes(forbidden),
      `the loader references ${forbidden} — it must read nothing from the host page`
    );
  }
});

test('the session token never appears in the iframe URL', () => {
  const { api } = mount({ sessionToken: 'dst_super_secret' });
  assert.ok(!api.frame.src.includes('dst_super_secret'), '§10: never in a URL, log or referrer');
  assert.ok(!JSON.stringify(api.frame.attrs).includes('dst_super_secret'));
});

test('the session token is handed over only after the widget says it is ready', () => {
  const { win } = mount({ sessionToken: 'dst_super_secret' });
  assert.equal(
    win._posted.filter((p) => p.message.payload.type === 'session').length,
    0,
    'the token must not be sent before the widget is listening'
  );

  win._deliver({
    origin: BASE,
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'ready' } }
  });

  const handovers = win._posted.filter((p) => p.message.payload.type === 'session');
  assert.equal(handovers.length, 1);
  assert.equal(handovers[0].message.payload.token, 'dst_super_secret');
  assert.equal(handovers[0].targetOrigin, BASE);
});

test('a forged ready from another origin does not extract the token', () => {
  const { win } = mount({ sessionToken: 'dst_super_secret' });
  win._deliver({
    origin: 'https://evil.example',
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'ready' } }
  });
  assert.equal(win._posted.filter((p) => p.message.payload.type === 'session').length, 0);
});

test('the token is handed over once, not on every ready', () => {
  const { win } = mount({ sessionToken: 'dst_super_secret' });
  const ready = {
    origin: BASE,
    source: win._contentWindow,
    data: { source: 'disputeshield-widget', version: 1, payload: { type: 'ready' } }
  };
  win._deliver(ready);
  win._deliver(ready);
  assert.equal(win._posted.filter((p) => p.message.payload.type === 'session').length, 1);
});
