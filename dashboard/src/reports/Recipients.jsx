import { useId, useState } from 'react';

/**
 * The report allowlist.
 *
 * Deliberately monochrome. DESIGN.md reserves colour for time, and nothing on
 * this screen is about a deadline — active and inactive are a text label and a
 * position in the list, not two hues.
 *
 * The form asks for a reason because the API requires one, and the help text
 * says why rather than treating it as a field to fill in. This is the screen
 * where somebody decides that a whole period's disclosure may leave for a given
 * address; the interface should read like that decision rather than like adding
 * a contact.
 */
export default function Recipients({ recipients, onAdd, onDeactivate, busy }) {
  const [address, setAddress] = useState('');
  const [label, setLabel] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const formId = useId();

  const active = recipients.filter((r) => r.is_active);
  const inactive = recipients.filter((r) => !r.is_active);

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      await onAdd({ address, label, reason });
      setAddress('');
      setLabel('');
      setReason('');
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <section aria-labelledby={`${formId}-heading`}>
      <h2 id={`${formId}-heading`} className="ds-h2">
        Recipients
      </h2>
      <p className="ds-lede">
        A regulatory export discloses every case in the period. It can only be sent to an address
        registered here, so where it may go is decided in advance rather than in the moment.
      </p>

      <table className="ds-table">
        <caption className="ds-visually-hidden">
          Registered report recipients, active first
        </caption>
        <thead>
          <tr>
            <th scope="col">Address</th>
            <th scope="col">What it is</th>
            <th scope="col">Registered by</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="ds-visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {[...active, ...inactive].map((recipient) => (
            <tr key={recipient.id} className={recipient.is_active ? '' : 'ds-row-inactive'}>
              <td className="ds-mono">{recipient.address}</td>
              <td>
                {recipient.label}
                <span className="ds-note">{recipient.reason}</span>
              </td>
              <td className="ds-num">{recipient.added_by}</td>
              <td>{recipient.is_active ? 'Active' : 'Deactivated'}</td>
              <td className="ds-actions">
                {recipient.is_active ? (
                  <button
                    type="button"
                    className="ds-button"
                    disabled={busy}
                    onClick={() => onDeactivate(recipient)}
                  >
                    Deactivate
                    <span className="ds-visually-hidden"> {recipient.address}</span>
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
          {recipients.length === 0 ? (
            <tr>
              <td colSpan={5} className="ds-empty">
                No recipients registered. Nothing can be emailed until one is.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <form className="ds-form" onSubmit={submit} aria-labelledby={`${formId}-add`}>
        <h3 id={`${formId}-add`} className="ds-h3">
          Register a recipient
        </h3>

        <div className="ds-field">
          <label htmlFor={`${formId}-address`}>Address</label>
          <input
            id={`${formId}-address`}
            className="ds-input"
            type="email"
            required
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
        </div>

        <div className="ds-field">
          <label htmlFor={`${formId}-label`}>What it is</label>
          <input
            id={`${formId}-label`}
            className="ds-input"
            required
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            aria-describedby={`${formId}-label-help`}
          />
          <p id={`${formId}-label-help`} className="ds-help">
            Shown wherever the address is. An address alone does not tell a reviewer whether it
            should be here.
          </p>
        </div>

        <div className="ds-field">
          <label htmlFor={`${formId}-reason`}>Reason</label>
          <input
            id={`${formId}-reason`}
            className="ds-input"
            required
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            aria-describedby={`${formId}-reason-help`}
          />
          <p id={`${formId}-reason-help`} className="ds-help">
            Recorded in the audit trail against your name. A recipient with no stated reason is one
            nobody can review later.
          </p>
        </div>

        {error ? (
          <p className="ds-error" role="alert">
            {error}
          </p>
        ) : null}

        <button type="submit" className="ds-button ds-button-primary" disabled={busy}>
          Register
        </button>
      </form>
    </section>
  );
}
