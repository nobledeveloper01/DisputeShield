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
