/**
 * DisputeShield loader — the only DisputeShield code that runs in the host page.
 *
 * It creates a sandboxed cross-origin iframe and nothing else. That is the whole
 * job, and the job is deliberately tiny: this file is what a fintech's engineer
 * reads before putting it on a page that handles money (ADR-0001), so it stays
 * small enough to read in full. CI enforces a 4 KB gzipped budget.
 *
 * What this file must never do, in any future version:
 *   - read the host page's DOM, forms, cookies or storage
 *   - hold, log or forward the session token (it belongs inside the iframe)
 *   - post a message with '*' as the target origin
 *   - fall back to inline rendering when the iframe cannot be created
 */
(function () {
  'use strict';

  var MOUNTED = '__disputeshield_mounted__';
  var PROTOCOL_VERSION = 1;

  // The full set of messages the widget may send us. Anything else is dropped.
  // An allowlist, not a switch with a default: a message type we do not know is
  // a message from something that is not our widget.
  var INBOUND = { ready: 1, resize: 1, close: 1, open: 1, error: 1 };

  function origin(url) {
    var a = document.createElement('a');
    a.href = url;
    return a.protocol + '//' + a.host;
  }

  function init(options) {
    options = options || {};

    if (window[MOUNTED]) {
      // Two script tags on one page must not produce two widgets, two message
      // listeners and two session tokens in memory.
      return window[MOUNTED];
    }

    if (!options.publishableKey) {
      return null; // Fail closed and silently (§8.6). Never render an error.
    }

    var base = options.baseUrl || 'https://widget.disputeshield.dev';
    var widgetOrigin = origin(base);

    var frame = document.createElement('iframe');
    frame.src =
      base +
      '/v1/embed?k=' +
      encodeURIComponent(options.publishableKey) +
      '&v=' +
      PROTOCOL_VERSION;

    // allow-same-origin is present so the widget can use its own storage and
    // talk to its own API. It does NOT grant access to the host page: the frame
    // is a different origin, so same-origin here means same-origin *with itself*.
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-same-origin');
    frame.setAttribute('allow', '');
    frame.setAttribute('referrerpolicy', 'strict-origin');
    frame.setAttribute('title', 'Report a problem');
    frame.setAttribute('loading', 'lazy');

    var style = frame.style;
    style.position = 'fixed';
    style.border = '0';
    style.width = '0';
    style.height = '0';
    style.zIndex = '2147483000';
    style.colorScheme = 'normal';
    positionFrame(style, options.position || 'bottom-right');

    // Held in a closure, never on `window`, never on the frame element, and
    // never in the iframe's URL. §10 is explicit that a session token must not
    // reach a URL, a log or a referrer — and an attribute on a DOM node in the
    // host's page is readable by every script in that page.
    var sessionToken = options.sessionToken || '';

    var api = {
      frame: frame,
      origin: widgetOrigin,
      open: function () {
        send({ type: 'open' });
      },
      close: function () {
        send({ type: 'close' });
      },
      destroy: function () {
        window.removeEventListener('message', onMessage);
        if (frame.parentNode) frame.parentNode.removeChild(frame);
        window[MOUNTED] = null;
      }
    };

    function send(message) {
      if (!frame.contentWindow) return;
      // Never '*'. Passing '*' here is the single most common way a widget
      // integration leaks data, and it would let any page that can reach this
      // frame read what we send it.
      frame.contentWindow.postMessage(
        { source: 'disputeshield-host', version: PROTOCOL_VERSION, payload: message },
        widgetOrigin
      );
    }

    function onMessage(event) {
      // Both checks, every time. The origin says who sent it; the envelope says
      // what it claims to be. Neither alone is enough — any page can post a
      // well-formed envelope, and any script in this page can post from this origin.
      if (event.origin !== widgetOrigin) return;
      if (event.source !== frame.contentWindow) return;

      var data = event.data;
      if (!data || data.source !== 'disputeshield-widget') return;
      if (data.version !== PROTOCOL_VERSION) return;

      var payload = data.payload;
      if (!payload || !INBOUND[payload.type]) return;

      if (payload.type === 'ready') {
        // Hand the token over only once the widget has said it is listening, and
        // only to its own origin. The widget keeps it in memory for its lifetime.
        if (sessionToken) {
          send({ type: 'session', token: sessionToken });
          sessionToken = '';
        }
      }

      if (payload.type === 'resize') {
        // Bounded (§8.6 principle 2). An unbounded height from the frame is a
        // way to cover the host's page with something the host did not intend.
        var w = clamp(payload.width, 0, 480);
        var h = clamp(payload.height, 0, Math.min(720, window.innerHeight));
        style.width = w + 'px';
        style.height = h + 'px';
      }

      if (typeof options.onEvent === 'function') {
        try {
          options.onEvent({ type: payload.type });
        } catch (e) {
          /* a host callback that throws is the host's problem, not ours */
        }
      }
    }

    function clamp(value, min, max) {
      value = typeof value === 'number' && isFinite(value) ? value : min;
      return Math.max(min, Math.min(max, value));
    }

    window.addEventListener('message', onMessage);
    (document.body || document.documentElement).appendChild(frame);

    window[MOUNTED] = api;
    return api;
  }

  function positionFrame(style, position) {
    var gap = '16px';
    style.bottom = position.indexOf('top') === 0 ? '' : gap;
    style.top = position.indexOf('top') === 0 ? gap : '';
    style.right = position.indexOf('left') === -1 ? gap : '';
    style.left = position.indexOf('left') === -1 ? '' : gap;
  }

  window.DisputeShield = { init: init, version: PROTOCOL_VERSION };
})();
