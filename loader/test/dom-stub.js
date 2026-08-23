import { readFileSync } from 'node:fs';

/**
 * A hand-rolled DOM, deliberately not jsdom.
 *
 * The loader touches five browser APIs. A stub that implements exactly those is
 * something a reviewer can read in a minute and be certain is not quietly
 * granting the loader something a real browser would refuse — which is the whole
 * question these tests exist to answer.
 */
export function makeWindow({ widgetOrigin = 'https://widget.disputeshield.dev' } = {}) {
  const listeners = { message: [] };
  const posted = [];

  const contentWindow = {
    postMessage(message, targetOrigin) {
      posted.push({ message, targetOrigin });
    }
  };

  const body = {
    children: [],
    appendChild(node) {
      this.children.push(node);
      node.parentNode = this;
    },
    removeChild(node) {
      this.children = this.children.filter((child) => child !== node);
      node.parentNode = null;
    }
  };

  const win = {
    innerHeight: 900,
    DisputeShield: undefined,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
    },
    document: {
      body,
      documentElement: body,
      createElement(tag) {
        const attrs = {};
        if (tag === 'a') {
          return {
            set href(value) {
              const m = /^(https?:)\/\/([^/]+)/.exec(value) || [];
              this.protocol = m[1] || 'https:';
              this.host = m[2] || '';
            }
          };
        }
        return {
          tagName: tag.toUpperCase(),
          style: {},
          attrs,
          contentWindow,
          parentNode: null,
          setAttribute(name, value) { attrs[name] = String(value); },
          getAttribute(name) { return attrs[name]; }
        };
      }
    },
    // Test helpers
    _deliver(event) { (listeners.message || []).forEach((fn) => fn(event)); },
    _posted: posted,
    _contentWindow: contentWindow,
    _listenerCount() { return (listeners.message || []).length; },
    _widgetOrigin: widgetOrigin
  };
  return win;
}

export function loadLoader(win) {
  // The loader is an IIFE that closes over `window` and `document`. Running it
  // with the stub bound to those names is the closest thing to loading a script
  // tag without pulling in a browser.
  const source = readLoaderSource();
  const fn = new Function('window', 'document', source);
  fn(win, win.document);
  return win.DisputeShield;
}

function readLoaderSource() {
  return readFileSync(new URL('../src/loader.js', import.meta.url), 'utf8');
}
