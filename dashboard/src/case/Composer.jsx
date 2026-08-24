import { useId, useState } from 'react';

/**
 * Two composers, not one composer with a toggle.
 *
 * A single box with a visibility dropdown is the design that produces the
 * accident this whole area is built to prevent: the agent types, the dropdown is
 * still set from last time, and an internal note goes to a customer — or a reply
 * the customer is waiting for is filed where they will never see it. Two
 * physically separate composers, each styled like the message it produces,
 * cannot be got wrong by muscle memory.
 */
export default function Composer({ visibility, onSend, busy }) {
  const [body, setBody] = useState('');
  const [error, setError] = useState('');
  const id = useId();
  const internal = visibility === 'internal';

  async function submit(event) {
    event.preventDefault();
    if (!body.trim()) return;
    setError('');
    try {
      await onSend(body, visibility);
      setBody('');
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <form
      className={`ds-composer ${internal ? 'ds-composer-internal' : 'ds-composer-customer'}`}
      onSubmit={submit}
      aria-labelledby={`${id}-legend`}
    >
      <h3 id={`${id}-legend`} className="ds-h3">
        {internal ? 'Add an internal note' : 'Reply to the customer'}
      </h3>
      <label className="ds-visually-hidden" htmlFor={`${id}-body`}>
        {internal ? 'Internal note body' : 'Reply to the customer'}
      </label>
      <textarea
        id={`${id}-body`}
        className="ds-input ds-textarea"
        rows={3}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        aria-describedby={`${id}-help`}
      />
      <p id={`${id}-help`} className="ds-help">
        {internal
          ? 'Never shown to the customer. Kept in the case record and in the regulatory export.'
          : 'Sent to the customer and shown in the widget.'}
      </p>
      {error ? (
        <p className="ds-error" role="alert">
          {error}
        </p>
      ) : null}
      <button type="submit" className="ds-button ds-button-primary" disabled={busy || !body.trim()}>
        {internal ? 'Save note' : 'Send reply'}
      </button>
    </form>
  );
}
