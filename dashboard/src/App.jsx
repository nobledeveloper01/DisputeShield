import { useCallback, useEffect, useState } from 'react';

import Recipients from './reports/Recipients.jsx';
import Schedules from './reports/Schedules.jsx';

/**
 * Reports · delivery.
 *
 * Two panels that only make sense together: the allowlist decides *where* an
 * export may go, and a schedule decides *when*. Splitting them across two
 * navigation entries would let somebody create a schedule without seeing that
 * the address it points at was deactivated last month.
 *
 * No skeleton screens (DESIGN.md). The list renders when it is known, because a
 * partially-rendered list of schedules invites a decision made on half the
 * picture.
 */
export default function App({ client }) {
  const [recipients, setRecipients] = useState(null);
  const [schedules, setSchedules] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([client.listRecipients(), client.listSchedules()]);
      setRecipients(r.data);
      setSchedules(s.data);
      setError('');
    } catch (problem) {
      setError(
        problem.status === 404
          ? 'This view is for compliance users. Your role cannot see report delivery.'
          : problem.message
      );
    }
  }, [client]);

  useEffect(() => {
    load();
  }, [load]);

  const guard = useCallback(
    async (action) => {
      setBusy(true);
      try {
        await action();
        await load();
      } finally {
        setBusy(false);
      }
    },
    [load]
  );

  if (error) {
    return (
      <main className="ds-app">
        <p className="ds-error" role="alert">
          {error}
        </p>
      </main>
    );
  }

  if (recipients === null || schedules === null) {
    return (
      <main className="ds-app">
        <p className="ds-note" role="status">
          Loading…
        </p>
      </main>
    );
  }

  return (
    <main className="ds-app">
      <header className="ds-app-header">
        <h1 className="ds-h1">Report delivery</h1>
        <p className="ds-lede">
          Where a regulatory export may be sent, and when it goes out without anybody asking.
        </p>
      </header>

      <Schedules
        schedules={schedules}
        recipients={recipients}
        busy={busy}
        onAdd={(payload) => guard(() => client.addSchedule(payload))}
        onDeactivate={(schedule) => guard(() => client.deactivateSchedule(schedule.id))}
      />

      <Recipients
        recipients={recipients}
        busy={busy}
        onAdd={(payload) => guard(() => client.addRecipient(payload))}
        onDeactivate={(recipient) => guard(() => client.deactivateRecipient(recipient.id))}
      />
    </main>
  );
}
