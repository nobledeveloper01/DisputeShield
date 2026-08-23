/**
 * The host side of the postMessage protocol, from inside the iframe.
 *
 * Symmetric with `loader/src/loader.js`: a fixed envelope, a version, an
 * allowlist of message types, and a target origin that is never '*'. Both sides
 * validate, because either side alone can be the compromised one.
 */

const PROTOCOL_VERSION = 1;
// Two allowlists, not one. What we may send and what we will accept are
// different questions, and answering them with a single set is how a message
// type added for one direction quietly becomes valid in the other.
const OUTBOUND = new Set(['ready', 'resize', 'close', 'open', 'error']);
const INBOUND = new Set(['open', 'close', 'session']);

export function createBridge(parentOrigin) {
  const canTalk = Boolean(parentOrigin) && parentOrigin !== 'null';

  function send(payload) {
    if (!canTalk) return;
    if (!OUTBOUND.has(payload.type)) return;
    window.parent.postMessage(
      { source: 'disputeshield-widget', version: PROTOCOL_VERSION, payload },
      // Never '*'. The host told us its origin in the embed document, and that
      // origin was checked against the tenant's allowlist before this document
      // was rendered at all.
      parentOrigin
    );
  }

  function listen(handler) {
    function onMessage(event) {
      if (event.origin !== parentOrigin) return;
      if (event.source !== window.parent) return;
      const data = event.data;
      if (!data || data.source !== 'disputeshield-host') return;
      if (data.version !== PROTOCOL_VERSION) return;
      if (!data.payload || !INBOUND.has(data.payload.type)) return;
      handler(data.payload);
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }

  return { send, listen, canTalk };
}

export function reportSize(bridge, element) {
  if (!element) return;
  const rect = element.getBoundingClientRect();
  bridge.send({
    type: 'resize',
    width: Math.ceil(rect.width),
    height: Math.ceil(rect.height)
  });
}
