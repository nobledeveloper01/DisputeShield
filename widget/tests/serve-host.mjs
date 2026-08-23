/**
 * Serves the hostile host page on its own origin.
 *
 * A different port is a different origin, which is the only thing these tests
 * need: the boundary under test is the browser's, and the browser does not care
 * that both servers happen to be on this machine.
 */
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.HOST_PORT || 4180);
const WIDGET_ORIGIN = process.env.WIDGET_ORIGIN || 'http://127.0.0.1:8011';

const SECRET_KEY = process.env.E2E_SECRET_KEY || 'ds_test_e2e_0000000000000000000000000000';

const template = readFileSync(join(here, 'fixtures', 'host.html'), 'utf8').replace(
  'window.__WIDGET_ORIGIN__',
  JSON.stringify(WIDGET_ORIGIN)
);

/**
 * Mints a session the way a fintech's backend does: server side, with the secret
 * key, scoped to one customer, with the transaction list supplied by us.
 *
 * The token is injected into the page for the loader to hand across. It never
 * reaches the iframe's URL, and the browser tests assert exactly that.
 */
async function mintSession() {
  const response = await fetch(`${WIDGET_ORIGIN}/v1/sessions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${SECRET_KEY}` },
    body: JSON.stringify({
      customer_ref: 'usr_e2e_9931',
      display_name: 'A. Okafor',
      transactions: [
        {
          reference: 'TXN-2026-08-11-8842',
          amount_minor: 5000000,
          currency: 'NGN',
          description: 'Transfer to GTBank ****4421',
          status: 'failed'
        }
      ]
    })
  });
  if (!response.ok) return '';
  return (await response.json()).session_token;
}
const loader = readFileSync(join(here, '..', '..', 'loader', 'dist', 'loader.js'), 'utf8');

createServer(async (req, res) => {
  if (req.url.startsWith('/loader.js')) {
    res.writeHead(200, { 'content-type': 'text/javascript' });
    res.end(loader);
    return;
  }
  const token = await mintSession().catch(() => '');
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(template.replace('__SESSION_TOKEN__', token));
}).listen(PORT, () => console.log(`host fixture on http://localhost:${PORT}`));
