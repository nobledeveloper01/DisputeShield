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

export const ANALYSIS = {
  group_by: 'category',
  summary: {
    cases: 412,
    breached: 27,
    resolved: 385,
    deflected: 96,
    recorded_refund_minor: 1_845_000_00,
    disputed_amount_minor: 9_120_000_00,
    average_pause_seconds: 9000,
    currencies: ['NGN']
  },
  rows: [
    // Deliberately not pre-sorted: the view sorts worst-first, and a fixture
    // that arrives sorted cannot tell whether it did.
    { key: 'failed_airtime', cases: 120, breached: 0, breach_rate: 0, total_pause_seconds: 0 },
    { key: 'failed_transfer', cases: 180, breached: 21, breach_rate: 0.1167, total_pause_seconds: 262800 },
    { key: 'duplicate_charge', cases: 112, breached: 6, breach_rate: 0.0536, total_pause_seconds: 5400 }
  ],
  causes: [
    { cause: 'Scheme response window exceeded; INC-2026-0412', cases: 14 },
    { cause: 'undocumented', cases: 9 },
    { cause: 'Beat scheduler stalled; INC-2026-0823', cases: 4 }
  ]
};

export const ANALYSIS_CLEAN = {
  ...ANALYSIS,
  summary: { ...ANALYSIS.summary, breached: 0 },
  rows: ANALYSIS.rows.map((row) => ({ ...row, breached: 0, breach_rate: 0 })),
  causes: [{ cause: 'Scheme response window exceeded; INC-2026-0412', cases: 2 }]
};

export const ANALYSIS_MIXED = {
  ...ANALYSIS,
  summary: { ...ANALYSIS.summary, currencies: ['NGN', 'USD'] }
};

const V1 = {
  id: 'ver_1',
  version: 1,
  calendar: 'Lagos business hours',
  calendar_timezone: 'Africa/Lagos',
  created_at: '2026-01-04T09:00:00+00:00',
  created_by: 'agt_adaeze',
  acknowledgement_minutes: 60,
  resolution_hours: 72,
  business_hours_only: true,
  warning_thresholds: [50, 80, 95],
  escalate_at_percent: 80,
  auto_close_after_hours: 168,
  reopen_window_hours: 336,
  regulatory_reference: 'CBN 2020 §3.1'
};
const V2 = { ...V1, id: 'ver_2', version: 2, resolution_hours: 168, created_at: '2026-04-02T11:30:00+00:00' };

export const POLICIES = {
  data: [
    {
      id: 'pol_transfer',
      category: 'failed_transfer',
      description: '',
      current: V2,
      version_count: 2
    },
    {
      id: 'pol_airtime',
      category: 'failed_airtime',
      description: '',
      current: { ...V1, id: 'ver_3' },
      version_count: 1
    }
  ],
  calendars: [{ id: 'cal_1', name: 'Lagos business hours', timezone: 'Africa/Lagos' }]
};

export const POLICY_DETAIL = {
  ...POLICIES.data[0],
  history: [
    { ...V2, changed: { resolution_hours: [72, 168] } },
    { ...V1, changed: {} }
  ]
};

export const WIDGET = {
  theme: {
    primary_colour: '#0B5FFF',
    radius: '8px',
    logo_url: '',
    position: 'bottom-right',
    locale: 'en'
  },
  positions: ['bottom-right', 'bottom-left'],
  categories: [
    { name: 'failed_transfer', has_policy: true },
    // The combination nothing in the data model prevents.
    { name: 'duplicate_charge', has_policy: false }
  ],
  policies_not_offered: ['atm_dispense_error'],
  origins: [
    { id: 'org_1', origin: 'https://app.acme.test' },
    { id: 'org_2', origin: 'https://checkout.acme.test' }
  ],
  frame_ancestors: "'self' https://app.acme.test https://checkout.acme.test",
  can_edit: true,
  can_change_origins: true
};

export const WIDGET_READONLY = { ...WIDGET, can_edit: false, can_change_origins: false };
export const WIDGET_SOUND = {
  ...WIDGET,
  categories: [{ name: 'failed_transfer', has_policy: true }]
};

export async function stubWidget(page, payload = WIDGET) {
  await page.route('**/v1/widget-config', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: payload })
      : route.fulfill({ json: payload })
  );
  await page.route('**/v1/widget-config/origins', (route) =>
    route.fulfill({ status: 201, json: { id: 'org_3', origin: 'https://new.acme.test' } })
  );
  await page.route('**/v1/widget-config/origins/*', (route) =>
    route.fulfill({ status: 204, body: '' })
  );
}

export async function stubPolicies(page) {
  await page.route('**/v1/sla-policies', (route) => route.fulfill({ json: POLICIES }));
  await page.route('**/v1/sla-policies/*', (route) =>
    route.fulfill({ json: POLICY_DETAIL })
  );
}

export async function stubAnalysis(page, payload = ANALYSIS) {
  await page.route('**/v1/analytics/sla-performance*', (route) => route.fulfill({ json: payload }));
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
