/**
 * The widget's API client.
 *
 * The session token lives here, in the iframe's own context, and is never
 * written to the URL, to storage the host could reach, or to a log. The host
 * page cannot read it — that is the boundary ADR-0001 exists to create — and
 * this file must not be the place that gives it away.
 */

export function createClient({ baseUrl, sessionToken }) {
  async function request(path, { method = 'GET', body, idempotencyKey } = {}) {
    const headers = { Accept: 'application/json' };
    if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
    if (body) headers['Content-Type'] = 'application/json';
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'omit',
      referrerPolicy: 'strict-origin'
    });

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      const error = new Error(detail?.error?.message || 'Request failed');
      error.status = response.status;
      error.type = detail?.error?.type;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  return {
    config: (key) => request(`/v1/widget/config`, { headers: { Authorization: `Bearer ${key}` } }),
    transactions: () => request('/v1/widget/disputes/transactions/'),
    listDisputes: () => request('/v1/widget/disputes/'),
    getDispute: (id) => request(`/v1/widget/disputes/${id}/`),
    fileDispute: (payload, idempotencyKey) =>
      request('/v1/widget/disputes/', { method: 'POST', body: payload, idempotencyKey }),
    addMessage: (id, body, idempotencyKey) =>
      request(`/v1/widget/disputes/${id}/messages/`, {
        method: 'POST',
        body: { body },
        idempotencyKey
      })
  };
}

export function newIdempotencyKey() {
  return crypto.randomUUID();
}
