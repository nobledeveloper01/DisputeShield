/**
 * How a policy's terms are described to the person changing them.
 *
 * Each field carries the consequence of getting it wrong, not a restatement of
 * its name. "Resolution window (hours)" tells a compliance officer nothing they
 * did not already know from the label; "every case filed under this policy is
 * judged against this window" tells them what they are about to change.
 */
export const FIELDS = [
  {
    name: 'acknowledgement_minutes',
    label: 'Acknowledgement window',
    unit: 'minutes',
    min: 1,
    help: 'How long the firm has to acknowledge a new complaint before the clock records a breach.'
  },
  {
    name: 'resolution_hours',
    label: 'Resolution window',
    unit: 'hours',
    min: 1,
    help: 'The mandated window. Every case filed under this policy is judged against it, and the version in force at filing is the one it keeps.'
  },
  {
    name: 'escalate_at_percent',
    label: 'Escalate at',
    unit: '% of the window',
    min: 1,
    max: 99,
    help: 'Capped below 100 on purpose: escalating at 100% escalates a case that has already breached, which is a notification rather than an escalation.'
  },
  {
    name: 'auto_close_after_hours',
    label: 'Auto-close after',
    unit: 'hours',
    min: 1,
    help: 'How long a resolved case waits before closing.'
  },
  {
    name: 'reopen_window_hours',
    label: 'Reopen window',
    unit: 'hours',
    min: 1,
    help: 'How long a customer has to reopen a closed case.'
  }
];

/** `[50, 80, 95]` from `"50, 80, 95"`, ignoring whatever else was typed. */
export function parseThresholds(text) {
  return [
    ...new Set(
      String(text || '')
        .split(/[,\s]+/)
        .map((piece) => Number.parseInt(piece, 10))
        .filter((value) => Number.isInteger(value))
    )
  ].sort((a, b) => a - b);
}

/**
 * The client-side check, which exists to explain rather than to enforce.
 *
 * The server validates the same rules and is the one that decides — this only
 * saves a round trip and says why before the officer presses the button. A
 * message here that disagrees with the server's would be worse than none.
 */
export function problemsWith(terms) {
  const problems = [];
  if (!(terms.resolution_hours >= 1)) {
    problems.push('A resolution window under an hour breaches every case the moment it is filed.');
  }
  if (!(terms.acknowledgement_minutes >= 1)) {
    problems.push('The acknowledgement window must be at least a minute.');
  }
  const thresholds = terms.warning_thresholds || [];
  if (thresholds.some((value) => value < 1 || value > 99)) {
    problems.push(
      'A warning threshold at or above 100% never fires. A policy that looks configured but warns nobody is worse than one with no warnings.'
    );
  }
  if (!(terms.escalate_at_percent >= 1 && terms.escalate_at_percent <= 99)) {
    problems.push('Escalation must sit below 100% of the window.');
  }
  return problems;
}

/** `resolution_hours` becomes `Resolution window`, for the change history. */
export function labelOf(field) {
  const known = FIELDS.find((entry) => entry.name === field);
  if (known) return known.label;
  if (field === 'warning_thresholds') return 'Warning thresholds';
  if (field === 'business_hours_only') return 'Business hours only';
  if (field === 'regulatory_reference') return 'Regulatory reference';
  return field;
}

/** How a changed value reads in the history: `72 → 168`. */
export function describeChange(field, [before, after]) {
  const show = (value) => {
    if (value === null || value === undefined) return 'unset';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    return String(value);
  };
  return `${show(before)} → ${show(after)}`;
}
