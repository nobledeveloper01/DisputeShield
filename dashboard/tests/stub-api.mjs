/**
 * The API responses these tests render against.
 *
 * Every address is under a reserved domain, as everywhere else in this
 * repository: `example.test` and `.invalid` can never resolve, so no fixture
 * here can be mistaken for — or accidentally become — a real destination.
 *
 * The schedule set covers one of each health state on purpose. An accessibility
 * run against only the healthy case would pass while never rendering the states
 * that carry colour, which are exactly the ones where colour contrast and a
 * non-colour encoding matter.
 */
export const RECIPIENTS = {
  data: [
    {
      id: 'rcp_1',
      address: 'compliance@example.test',
      label: 'Internal compliance archive',
      reason: 'Retained alongside the firm’s own records.',
      is_active: true,
      added_by: 'agt_adaeze',
      created_at: '2026-03-01T09:00:00+00:00'
    },
    {
      id: 'rcp_2',
      address: 'supervision@example.test',
      label: 'Supervisory returns inbox',
      reason: 'Quarterly supervisory request.',
      is_active: true,
      added_by: 'agt_adaeze',
      created_at: '2026-03-01T09:00:00+00:00'
    },
    {
      id: 'rcp_3',
      address: 'former-auditor@example.invalid',
      label: 'Previous external auditor',
      reason: 'Engagement ended.',
      is_active: false,
      added_by: 'agt_adaeze',
      created_at: '2026-01-04T09:00:00+00:00'
    }
  ]
};

export const SCHEDULES = {
  data: [
    {
      id: 'sch_ok',
      name: 'Aardvark healthy schedule',
      recipients: ['compliance@example.test'],
      day_of_month: 5,
      hour: 6,
      timezone: 'UTC',
      is_active: true,
      reason: 'Standing arrangement.',
      created_by: 'agt_adaeze',
      last_period_delivered: '2026-04-01',
      next_period: '2026-05-01',
      failed_periods: [],
      periods_owed: [],
      is_overdue: false
    },
    {
      id: 'sch_owed',
      name: 'Bravo due schedule',
      recipients: ['supervision@example.test'],
      day_of_month: 5,
      hour: 6,
      timezone: 'Europe/London',
      is_active: true,
      reason: 'Standing arrangement.',
      created_by: 'agt_adaeze',
      last_period_delivered: '2026-04-01',
      next_period: '2026-05-01',
      failed_periods: [],
      periods_owed: ['2026-05-01'],
      is_overdue: false
    },
    {
      id: 'sch_overdue',
      name: 'Charlie overdue schedule',
      recipients: ['compliance@example.test'],
      day_of_month: 5,
      hour: 6,
      timezone: 'UTC',
      is_active: true,
      reason: 'Standing arrangement.',
      created_by: 'agt_adaeze',
      last_period_delivered: '2026-02-01',
      next_period: '2026-03-01',
      failed_periods: [],
      periods_owed: ['2026-03-01', '2026-04-01'],
      is_overdue: true
    },
    {
      id: 'sch_failed',
      name: 'Delta abandoned a month',
      recipients: ['supervision@example.test'],
      day_of_month: 5,
      hour: 6,
      timezone: 'UTC',
      is_active: true,
      reason: 'Standing arrangement.',
      created_by: 'agt_adaeze',
      last_period_delivered: '2026-01-01',
      next_period: '2026-02-01',
      failed_periods: [{ period: '2026-02-01', attempts: 3, last_error: 'BundleChanged: ...' }],
      periods_owed: ['2026-03-01'],
      is_overdue: true
    },
    {
      id: 'sch_paused',
      name: 'Echo deactivated schedule',
      recipients: ['compliance@example.test'],
      day_of_month: 5,
      hour: 6,
      timezone: 'UTC',
      is_active: false,
      reason: 'Superseded.',
      created_by: 'agt_adaeze',
      last_period_delivered: '2026-04-01',
      next_period: '2026-05-01',
      failed_periods: [],
      periods_owed: [],
      is_overdue: false
    }
  ]
};

/**
 * A queue with one case in every clock state, and a fixed `now` so the states
 * are stable. Deadlines are expressed relative to that instant when the stub is
 * installed, because a fixture with hard-coded timestamps starts reporting
 * everything as breached the week after it is written.
 */
export function queuePage(now = Date.now()) {
  const at = (seconds) => new Date(now + seconds * 1000).toISOString();
  const row = (id, reference, remaining, window, extra = {}) => ({
    id,
    reference,
    category: 'failed_transfer',
    customer_display_name: 'A. Customer',
    customer_ref_hash: 'c0ffee0000000000',
    amount_minor: 4500000,
    currency: 'NGN',
    status: 'investigating',
    assigned_to: null,
    submitted_at: at(remaining - window),
    sla: {
      state: 'running',
      resolution_deadline: at(remaining),
      ack_deadline: at(remaining - window / 2),
      breached: false,
      ...extra
    }
  });

  return {
    results: [
      // Server order: breached pinned, then by deadline. The client does not
      // re-sort, so the fixture arrives in the order the server would send.
      row('dsp_breached', 'DS-2026-BREACH', -8040, 72 * 3600, { breached: true }),
      row('dsp_critical', 'DS-2026-CRIT', 2 * 3600, 72 * 3600),
      row('dsp_warning', 'DS-2026-WARN', 10 * 3600, 72 * 3600),
      row('dsp_notice', 'DS-2026-NOTE', 30 * 3600, 72 * 3600),
      row('dsp_paused', 'DS-2026-PAUSE', 22 * 3600, 72 * 3600, { state: 'paused' }),
      row('dsp_calm', 'DS-2026-CALM', 60 * 3600, 72 * 3600)
    ]
  };
}

export function caseDetail(now = Date.now()) {
  const at = (seconds) => new Date(now + seconds * 1000).toISOString();
  return {
    ...queuePage(now).results[2],
    id: 'dsp_warning',
    description: 'My transfer failed but I was debited.',
    transaction_ref: 'txn_9f2c',
    sla: {
      ...queuePage(now).results[2].sla,
      remaining_seconds: 3 * 3600 + 20 * 60,
      paused_intervals: 1
    },
    messages: [
      {
        id: 'msg_1',
        visibility: 'customer',
        author_type: 'customer',
        body: 'I still have not received my money.',
        created_at: at(-7200)
      },
      {
        id: 'msg_2',
        visibility: 'internal',
        author_type: 'agent',
        body: 'Ledger shows the debit but no reversal. Escalating to payments.',
        created_at: at(-3600)
      },
      {
        id: 'msg_3',
        visibility: 'customer',
        author_type: 'agent',
        body: 'We are looking into this and will update you.',
        created_at: at(-1800)
      }
    ]
  };
}

export const CONTEXT = [
  {
    id: 'ctx_1',
    source: 'ledger',
    occurred_at: '2026-04-29T09:14:00+00:00',
    summary: 'Debit posted, no matching reversal.',
    detail: {}
  }
];

export async function stubQueue(page, now = Date.now()) {
  await page.route('**/v1/disputes/?*', (route) => route.fulfill({ json: queuePage(now) }));
  await page.route('**/v1/disputes/', (route) => route.fulfill({ json: queuePage(now) }));
  await page.route('**/v1/disputes/*/context/', (route) => route.fulfill({ json: CONTEXT }));
  await page.route('**/v1/disputes/*/messages/', (route) =>
    route.fulfill({ status: 201, json: caseDetail(now).messages[0] })
  );
  await page.route('**/v1/disputes/*/pause/', (route) =>
    route.fulfill({ json: caseDetail(now) })
  );
  await page.route('**/v1/disputes/*/resume/', (route) =>
    route.fulfill({ json: caseDetail(now) })
  );
  await page.route('**/v1/disputes/*/', (route) => route.fulfill({ json: caseDetail(now) }));
}

export async function stubApi(page) {
  await page.route('**/v1/reports/recipients', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: RECIPIENTS })
      : route.fulfill({ status: 201, json: RECIPIENTS.data[0] })
  );
  await page.route('**/v1/reports/recipients/*', (route) =>
    route.fulfill({ json: { ...RECIPIENTS.data[0], is_active: false } })
  );
  await page.route('**/v1/reports/schedules', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: SCHEDULES })
      : route.fulfill({ status: 201, json: SCHEDULES.data[0] })
  );
  await page.route('**/v1/reports/schedules/*', (route) =>
    route.fulfill({ json: { ...SCHEDULES.data[0], is_active: false } })
  );
}
