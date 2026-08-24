import { useCallback, useEffect, useState } from 'react';

import Analysis from './analysis/Analysis.jsx';
import Case from './case/Case.jsx';
import Policies from './policies/Policies.jsx';
import Queue from './queue/Queue.jsx';
import Recipients from './reports/Recipients.jsx';
import Schedules from './reports/Schedules.jsx';
import { useRoute } from './router.js';

/**
 * The dashboard shell.
 *
 * Three routes: the queue an agent works all day, a case, and the compliance
 * view for report delivery. Navigation is monochrome — colour is reserved for
 * time, and a coloured nav item competes with the only pixels that are supposed
 * to be shouting.
 */
export default function App({ client }) {
  const route = useRoute();

  return (
    <>
      <nav className="ds-nav" aria-label="Sections">
        <a className="ds-link" href="#/" aria-current={route.name === 'queue' ? 'page' : undefined}>
          Queue
        </a>
        <a
          className="ds-link"
          href="#/policies"
          aria-current={route.name === 'policies' ? 'page' : undefined}
        >
          SLA policies
        </a>
        <a
          className="ds-link"
          href="#/analysis"
          aria-current={route.name === 'analysis' ? 'page' : undefined}
        >
          Breach analysis
        </a>
        <a
          className="ds-link"
          href="#/reports"
          aria-current={route.name === 'reports' ? 'page' : undefined}
        >
          Report delivery
        </a>
      </nav>

      {route.name === 'queue' ? <QueueScreen client={client} /> : null}
      {route.name === 'case' ? <CaseScreen client={client} id={route.id} /> : null}
      {route.name === 'policies' ? <PoliciesScreen client={client} /> : null}
      {route.name === 'analysis' ? <AnalysisScreen client={client} /> : null}
      {route.name === 'reports' ? <ReportsScreen client={client} /> : null}
    </>
  );
}

function QueueScreen({ client }) {
  const [disputes, setDisputes] = useState(null);
  const [filters, setFilters] = useState({ sla_risk: '', open: 'true' });
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    client
      .listQueue(filters)
      // No skeleton screen: the list appears when the sort order is known,
      // because a partially-rendered queue invites action on a partial sort.
      .then((page) => !cancelled && setDisputes(page.results || page.data || []))
      .catch((problem) => !cancelled && setError(problem.message));
    return () => {
      cancelled = true;
    };
  }, [client, filters]);

  if (error) return <Failed message={error} />;
  if (disputes === null) return <Loading />;

  return (
    <main className="ds-app ds-app-wide">
      <Queue disputes={disputes} filters={filters} onFilter={setFilters} busy={false} />
    </main>
  );
}

function CaseScreen({ client, id }) {
  const [dispute, setDispute] = useState(null);
  const [context, setContext] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [one, entries] = await Promise.all([client.getCase(id), client.getContext(id)]);
      setDispute(one);
      setContext(entries);
      setError('');
    } catch (problem) {
      setError(
        problem.status === 404
          ? 'No such case. It may belong to another tenant, or it may not exist — from here those are the same answer.'
          : problem.message
      );
    }
  }, [client, id]);

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

  if (error) return <Failed message={error} />;
  if (dispute === null) return <Loading />;

  return (
    <main className="ds-app ds-app-wide">
      <Case
        dispute={dispute}
        context={context}
        busy={busy}
        onSend={(body, visibility) => guard(() => client.addMessage(id, body, visibility))}
        onPause={(reason) => guard(() => client.pause(id, reason))}
        onResume={(reason) => guard(() => client.resume(id, reason))}
      />
    </main>
  );
}

function Loading() {
  return (
    <main className="ds-app">
      <p className="ds-note" role="status">
        Loading…
      </p>
    </main>
  );
}

function Failed({ message }) {
  return (
    <main className="ds-app">
      <p className="ds-error" role="alert">
        {message}
      </p>
    </main>
  );
}

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
function PoliciesScreen({ client }) {
  const [list, setList] = useState(null);
  const [calendars, setCalendars] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const page = await client.listPolicies();
      setList(page.data);
      setCalendars(page.calendars || []);
      const id = selected || page.data[0]?.id || null;
      setSelected(id);
      // The list gives current terms; the detail adds the change history, which
      // is what makes this screen worth opening.
      setDetail(id ? await client.getPolicy(id) : null);
      setError('');
    } catch (problem) {
      setError(problem.message);
    }
  }, [client, selected]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <Failed message={error} />;
  if (list === null) return <Loading />;

  const merged = list.map((entry) => (entry.id === detail?.id ? detail : entry));

  return (
    <main className="ds-app ds-app-wide">
      <Policies
        policies={merged}
        calendars={calendars}
        selected={selected}
        busy={busy}
        onSelect={setSelected}
        onPublish={async (id, terms) => {
          setBusy(true);
          try {
            await client.publishPolicy(id, terms);
            await load();
          } finally {
            setBusy(false);
          }
        }}
      />
    </main>
  );
}

function AnalysisScreen({ client }) {
  const [data, setData] = useState(null);
  const [groupBy, setGroupBy] = useState('category');
  const [period, setPeriod] = useState(() => defaultPeriod());
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    client
      .slaPerformance({ from: period.from, to: period.to, group_by: groupBy })
      .then((result) => !cancelled && setData(result))
      .catch((problem) => !cancelled && setError(problem.message));
    return () => {
      cancelled = true;
    };
  }, [client, period, groupBy]);

  if (error) return <Failed message={error} />;
  if (data === null) return <Loading />;

  return (
    <main className="ds-app ds-app-wide">
      <Analysis
        data={data}
        period={period}
        groupBy={groupBy}
        onPeriod={setPeriod}
        onGroupBy={setGroupBy}
      />
    </main>
  );
}

/**
 * The last complete calendar month, not "the last 30 days".
 *
 * A regulatory conversation is about periods a supervisor recognises, and a
 * rolling window means the same question asked twice a day apart gets two
 * different answers with no way to tell which was quoted.
 */
function defaultPeriod(now = new Date()) {
  const firstOfThis = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const firstOfLast = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  return { from: iso(firstOfLast), to: iso(firstOfThis) };
}

function iso(date) {
  return date.toISOString().slice(0, 10);
}

function ReportsScreen({ client }) {
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
