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
    listQueue: (filters = {}) => {
      const query = new URLSearchParams(
        Object.entries(filters).filter(([, value]) => value !== '' && value !== undefined)
      );
      const suffix = query.toString();
      return request(`/v1/disputes/${suffix ? `?${suffix}` : ''}`);
    },
    getCase: (id) => request(`/v1/disputes/${id}/`),
    getContext: (id) => request(`/v1/disputes/${id}/context/`),
    addMessage: (id, body, visibility) =>
      request(`/v1/disputes/${id}/messages/`, { method: 'POST', body: { body, visibility } }),
    pause: (id, reason) => request(`/v1/disputes/${id}/pause/`, { method: 'POST', body: { reason } }),
    resume: (id, reason) =>
      request(`/v1/disputes/${id}/resume/`, { method: 'POST', body: { reason } }),

    apiKeys: () => request('/v1/api-keys'),
    createKey: (payload) => request('/v1/api-keys', { method: 'POST', body: payload }),
    revokeKey: (id) => request(`/v1/api-keys/${id}`, { method: 'DELETE' }),
    team: () => request('/v1/agents'),
    addMember: (payload) => request('/v1/agents', { method: 'POST', body: payload }),
    changeMember: (id, payload) =>
      request(`/v1/agents/${id}`, { method: 'PATCH', body: payload }),
    retention: () => request('/v1/retention'),

    widgetConfig: () => request('/v1/widget-config'),
    saveWidgetConfig: (payload) =>
      request('/v1/widget-config', { method: 'PATCH', body: payload }),
    addOrigin: (origin) =>
      request('/v1/widget-config/origins', { method: 'POST', body: { origin } }),
    removeOrigin: (id) =>
      request(`/v1/widget-config/origins/${id}`, { method: 'DELETE' }),

    listPolicies: () => request('/v1/sla-policies'),
    getPolicy: (id) => request(`/v1/sla-policies/${id}`),
    publishPolicy: (id, terms) =>
      request(`/v1/sla-policies/${id}`, { method: 'PATCH', body: terms }),

    slaPerformance: ({ from, to, group_by }) => {
      const query = new URLSearchParams(
        Object.entries({ from, to, group_by }).filter(([, v]) => v)
      );
      return request(`/v1/analytics/sla-performance?${query.toString()}`);
    },

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
