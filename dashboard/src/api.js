/**
 * The dashboard's API client.
 *
 * Credentials are the session cookie the agent already holds, so nothing here
 * handles a key. `credentials: 'same-origin'` rather than `'include'`: this
 * console only ever talks to its own deployment, and a client that would send
 * credentials cross-origin is one misconfiguration away from doing so.
 */
export function createClient({ baseUrl }) {
  async function request(path, { method = 'GET', body } = {}) {
    const headers = { Accept: 'application/json' };
    if (body) headers['Content-Type'] = 'application/json';

    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
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
    listRecipients: () => request('/v1/reports/recipients'),
    addRecipient: (payload) =>
      request('/v1/reports/recipients', { method: 'POST', body: payload }),
    deactivateRecipient: (id) =>
      request(`/v1/reports/recipients/${id}`, { method: 'DELETE' }),

    listSchedules: () => request('/v1/reports/schedules'),
    addSchedule: (payload) => request('/v1/reports/schedules', { method: 'POST', body: payload }),
    deactivateSchedule: (id) =>
      request(`/v1/reports/schedules/${id}`, { method: 'DELETE' })
  };
}
