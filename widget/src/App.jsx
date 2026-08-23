import { useCallback, useEffect, useRef, useState } from 'react';

import { createClient, newIdempotencyKey } from './api.js';
import { createBridge, reportSize } from './bridge.js';

/**
 * One decision per screen (DESIGN.md). A customer who has lost money is not
 * reading a form, so the flow asks which transaction, then what happened, then
 * confirms — and tells them the expected resolution date before they submit,
 * because that commitment is a regulatory quantity they should hear from us
 * rather than from silence.
 */
const STEPS = ['transaction', 'detail', 'review', 'filed'];

export default function App({ publishableKey, parentOrigin, baseUrl }) {
  const [step, setStep] = useState('transaction');
  const [transactions, setTransactions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [category, setCategory] = useState('');
  const [categories, setCategories] = useState([]);
  const [description, setDescription] = useState('');
  const [filed, setFiled] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const rootRef = useRef(null);
  const headingRef = useRef(null);
  const bridge = useRef(createBridge(parentOrigin)).current;
  // The token arrives over postMessage from the host, never in this document's
  // URL (§10). It lives in a ref for the widget's lifetime and is never written
  // to storage the host could reach.
  const [sessionToken, setSessionToken] = useState('');
  const client = useRef(null);
  if (client.current === null || client.current.token !== sessionToken) {
    client.current = Object.assign(createClient({ baseUrl, sessionToken }), {
      token: sessionToken
    });
  }
  // One key per filing attempt, not per click. A retry after a network timeout
  // must not create a second case for the same complaint (§8.6 principle 4).
  const idempotencyKey = useRef(newIdempotencyKey());

  useEffect(
    () =>
      bridge.listen((message) => {
        if (message.type === 'session') setSessionToken(message.token || '');
        if (message.type === 'close') bridge.send({ type: 'close' });
      }),
    [bridge]
  );

  useEffect(() => {
    bridge.send({ type: 'ready' });
  }, [bridge]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [txns, config] = await Promise.all([
          sessionToken
            ? client.current.transactions().catch(() => ({ transactions: [] }))
            : Promise.resolve({ transactions: [] }),
          fetch(`${baseUrl}/v1/widget/config`, {
            headers: { Authorization: `Bearer ${publishableKey}` }
          })
            .then((r) => (r.ok ? r.json() : { categories: [] }))
            .catch(() => ({ categories: [] }))
        ]);
        if (cancelled) return;
        setTransactions(txns.transactions || []);
        setCategories(config.categories || []);
        setCategory((config.categories || [])[0] || '');
      } catch {
        // Fail closed and quietly. Never render a broken interface on a
        // fintech's page (§8.6 principle 1).
        if (!cancelled) setError('unavailable');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [baseUrl, publishableKey, sessionToken]);

  useEffect(() => {
    reportSize(bridge, rootRef.current);
    // Move focus to the new step's heading so a screen reader announces it and
    // a keyboard user is not left at the top of the document.
    headingRef.current?.focus();
  }, [step, bridge, transactions.length]);

  const submit = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const dispute = await client.current.fileDispute(
        {
          category,
          description,
          transaction_ref: selected?.reference || ''
        },
        idempotencyKey.current
      );
      setFiled(dispute);
      setStep('filed');
    } catch (e) {
      setError(e.message || 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }, [category, description, selected]);

  if (error === 'unavailable') return null;

  return (
    <div className="ds-root" ref={rootRef}>
      <header className="ds-header">
        <h1 className="ds-title" tabIndex={-1} ref={headingRef}>
          {titleFor(step)}
        </h1>
        <button
          type="button"
          className="ds-close"
          onClick={() => bridge.send({ type: 'close' })}
          aria-label="Close"
        >
          ×
        </button>
      </header>

      {step === 'transaction' && (
        <TransactionStep
          transactions={transactions}
          selected={selected}
          onSelect={setSelected}
          onNext={() => setStep('detail')}
        />
      )}

      {step === 'detail' && (
        <DetailStep
          categories={categories}
          category={category}
          setCategory={setCategory}
          description={description}
          setDescription={setDescription}
          onBack={() => setStep('transaction')}
          onNext={() => setStep('review')}
        />
      )}

      {step === 'review' && (
        <ReviewStep
          selected={selected}
          category={category}
          description={description}
          busy={busy}
          error={error}
          onBack={() => setStep('detail')}
          onSubmit={submit}
        />
      )}

      {step === 'filed' && <FiledStep dispute={filed} />}

      <ol className="ds-progress" aria-label="Progress">
        {STEPS.slice(0, 3).map((name, index) => (
          <li key={name} aria-current={step === name ? 'step' : undefined}>
            <span className="ds-visually-hidden">Step {index + 1}: </span>
            {titleFor(name)}
          </li>
        ))}
      </ol>
    </div>
  );
}

function TransactionStep({ transactions, selected, onSelect, onNext }) {
  return (
    <section>
      <p className="ds-hint">Pick the transaction you are reporting.</p>
      {transactions.length === 0 ? (
        <p className="ds-empty">No recent transactions to show.</p>
      ) : (
        // A div, not a ul. Putting role="radiogroup" on a list strips its
        // implicit list semantics and orphans the li children — axe catches it,
        // and a screen reader announces a list item outside any list.
        <div className="ds-list" role="radiogroup" aria-label="Recent transactions">
          {transactions.map((txn) => (
            <button
              key={txn.reference}
              type="button"
              role="radio"
              aria-checked={selected?.reference === txn.reference}
              className="ds-option"
              onClick={() => onSelect(txn)}
            >
              <span className="ds-option-desc">{txn.description || txn.reference}</span>
              <span className="ds-option-amount">{formatAmount(txn)}</span>
            </button>
          ))}
        </div>
      )}
      <button type="button" className="ds-primary" onClick={onNext}>
        Continue
      </button>
    </section>
  );
}

function DetailStep({ categories, category, setCategory, description, setDescription, onBack, onNext }) {
  return (
    <section>
      <label className="ds-label" htmlFor="ds-category">
        What went wrong?
      </label>
      <select
        id="ds-category"
        className="ds-input"
        value={category}
        onChange={(e) => setCategory(e.target.value)}
      >
        {categories.map((name) => (
          <option key={name} value={name}>
            {humanise(name)}
          </option>
        ))}
      </select>

      <label className="ds-label" htmlFor="ds-description">
        Tell us what happened
      </label>
      <textarea
        id="ds-description"
        className="ds-input"
        rows={4}
        maxLength={10000}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />

      <div className="ds-actions">
        <button type="button" className="ds-secondary" onClick={onBack}>
          Back
        </button>
        <button
          type="button"
          className="ds-primary"
          disabled={!description.trim()}
          onClick={onNext}
        >
          Continue
        </button>
      </div>
    </section>
  );
}

function ReviewStep({ selected, category, description, busy, error, onBack, onSubmit }) {
  return (
    <section>
      <dl className="ds-summary">
        <dt>Transaction</dt>
        <dd>{selected ? selected.description || selected.reference : 'Not specified'}</dd>
        <dt>Problem</dt>
        <dd>{humanise(category)}</dd>
        <dt>Your description</dt>
        <dd>{description}</dd>
      </dl>
      {error && (
        <p className="ds-error" role="alert">
          {error}
        </p>
      )}
      <div className="ds-actions">
        <button type="button" className="ds-secondary" onClick={onBack}>
          Back
        </button>
        <button type="button" className="ds-primary" disabled={busy} onClick={onSubmit}>
          {busy ? 'Submitting…' : 'Submit report'}
        </button>
      </div>
    </section>
  );
}

function FiledStep({ dispute }) {
  return (
    <section>
      <p className="ds-reference">
        Your reference is <strong>{dispute?.reference}</strong>
      </p>
      {/* §3.2 A3: the commitment is a regulatory quantity. The customer hears it
          from us rather than from silence. */}
      {dispute?.expected_resolution_at && (
        <p className="ds-hint">
          We will come back to you by{' '}
          <time dateTime={dispute.expected_resolution_at}>
            {formatDate(dispute.expected_resolution_at)}
          </time>
          .
        </p>
      )}
    </section>
  );
}

function titleFor(step) {
  return {
    transaction: 'Which transaction?',
    detail: 'What happened?',
    review: 'Check and send',
    filed: 'Report received'
  }[step];
}

function humanise(name) {
  return String(name || '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

function formatAmount(txn) {
  if (txn.amount_minor == null) return '';
  // Integer minor units throughout. Money is not a float, and a widget that
  // rounds a customer's disputed amount is a widget that starts an argument.
  const major = (txn.amount_minor / 100).toFixed(2);
  return `${txn.currency || ''} ${major}`.trim();
}

function formatDate(iso) {
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'long', timeStyle: 'short' }).format(
      new Date(iso)
    );
  } catch {
    return iso;
  }
}
