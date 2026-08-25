import { useState } from 'react';

import { formatWhen, humanise } from '../format.js';
import { countKeys, maskedKey, memberBlock, revokeWarning } from './rules.js';

/**
 * Settings: keys, the team, retention and sign-in.
 *
 * Monochrome throughout. Nothing on this screen is a deadline, and DESIGN.md
 * reserves colour for time.
 *
 * The screen's shape follows from one thing: every action here is either
 * irreversible or capable of locking somebody out, and none of them is urgent.
 * So each control states its consequence *before* it is used rather than
 * explaining itself in a refusal afterwards, and the two team rules the server
 * enforces are mirrored here as disabled controls with the reason attached.
 */
export default function Settings({
  keys,
  team,
  retention,
  busy,
  onCreateKey,
  onRevokeKey,
  onAddMember,
  onChangeMember
}) {
  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading" className="ds-h1">
        Settings
      </h1>
      <p className="ds-lede">
        Credentials, who can sign in, and where this tenant stands against its retention
        obligation.
      </p>

      <Keys keys={keys} busy={busy} onCreate={onCreateKey} onRevoke={onRevokeKey} />
      <Team team={team} busy={busy} onAdd={onAddMember} onChange={onChangeMember} />
      <Retention retention={retention} />
      <SignIn />
    </section>
  );
}

function Keys({ keys, busy, onCreate, onRevoke }) {
  const [name, setName] = useState('');
  const [environment, setEnvironment] = useState('test');
  const [kind, setKind] = useState('secret');
  const [minted, setMinted] = useState(null);
  const [error, setError] = useState('');
  const counts = countKeys(keys.data);

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      // Held in component state and nowhere else. Not in the URL, not in
      // storage, and gone the moment this component unmounts.
      setMinted(await onCreate({ name, environment, kind }));
      setName('');
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <section aria-labelledby="keys-heading" className="ds-settings-block">
      <h2 id="keys-heading" className="ds-h2">
        API keys
      </h2>
      <p className="ds-lede">
        {counts.live} live and {counts.test} test {counts.live + counts.test === 1 ? 'key' : 'keys'}{' '}
        active. A key is shown once, when it is created — only a hash is stored, so it cannot be
        shown again.
      </p>

      {minted ? (
        <div className="ds-panel ds-minted" role="alert">
          <h3 className="ds-h3">Copy this now. It will not be shown again.</h3>
          <p className="ds-note">
            {minted.name} · {minted.environment} · {minted.kind}
          </p>
          <output className="ds-secret ds-mono">{minted.key}</output>
          <p className="ds-help">
            Only a hash of this value is stored, so there is no way to retrieve it later. If it is
            lost, revoke this key and issue another.
          </p>
          <button type="button" className="ds-button" onClick={() => setMinted(null)}>
            I have copied it
          </button>
        </div>
      ) : null}

      <table className="ds-table">
        <caption className="ds-visually-hidden">API keys, active first</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Key</th>
            <th scope="col">Environment</th>
            <th scope="col">Last used</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="ds-visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {keys.data.map((key) => {
            const warning = revokeWarning(key, keys.data);
            return (
              <tr key={key.id} className={key.is_active ? '' : 'ds-row-inactive'}>
                <td>
                  {key.name}
                  {key.is_current ? <span className="ds-note">this session</span> : null}
                </td>
                <td className="ds-mono">{maskedKey(key.prefix)}</td>
                <td>{key.environment}</td>
                <td className="ds-num">{key.last_used_at ? formatWhen(key.last_used_at) : 'never'}</td>
                <td>{key.is_active ? 'Active' : `Revoked ${formatWhen(key.revoked_at)}`}</td>
                <td className="ds-actions">
                  {key.is_active && keys.can_manage ? (
                    <button
                      type="button"
                      className="ds-button"
                      disabled={busy}
                      title={warning || undefined}
                      onClick={() => {
                        if (!warning || window.confirm(warning)) onRevoke(key);
                      }}
                    >
                      Revoke
                      <span className="ds-visually-hidden"> {key.name}</span>
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
          {keys.data.length === 0 ? (
            <tr>
              <td colSpan={6} className="ds-empty">
                No keys. Nothing can call the API.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      {keys.can_manage ? (
        <form className="ds-form" onSubmit={submit} aria-labelledby="new-key">
          <h3 id="new-key" className="ds-h3">
            Issue a key
          </h3>
          <div className="ds-row">
            <div className="ds-field">
              <label htmlFor="key-name">Name</label>
              <input
                id="key-name"
                className="ds-input"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-describedby="key-name-help"
              />
              <p id="key-name-help" className="ds-help">
                What uses it. An unnamed key is one nobody can safely revoke.
              </p>
            </div>
            <div className="ds-field">
              <label htmlFor="key-env">Environment</label>
              <select
                id="key-env"
                className="ds-input"
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
                aria-describedby="key-env-help"
              >
                <option value="test">test</option>
                <option value="live">live</option>
              </select>
              <p id="key-env-help" className="ds-help">
                A leaked test key can do nothing to live data.
              </p>
            </div>
            <div className="ds-field">
              <label htmlFor="key-kind">Kind</label>
              <select
                id="key-kind"
                className="ds-input"
                value={kind}
                onChange={(e) => setKind(e.target.value)}
                aria-describedby="key-kind-help"
              >
                <option value="secret">secret</option>
                <option value="publishable">publishable</option>
              </select>
              <p id="key-kind-help" className="ds-help">
                Publishable keys are safe in a page. Secret keys never are.
              </p>
            </div>
          </div>
          {error ? (
            <p className="ds-error" role="alert">
              {error}
            </p>
          ) : null}
          <button type="submit" className="ds-button ds-button-primary" disabled={busy || !name}>
            Issue key
          </button>
        </form>
      ) : (
        <p className="ds-help">Only an owner can issue or revoke keys.</p>
      )}
    </section>
  );
}

function Team({ team, busy, onAdd, onChange }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('agent');
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      await onAdd({ email, role });
      setEmail('');
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <section aria-labelledby="team-heading" className="ds-settings-block">
      <h2 id="team-heading" className="ds-h2">
        Team
      </h2>
      <p className="ds-lede">
        An agent can resolve a case but not change an SLA policy. A compliance user can change a
        policy and export a period. Only an owner can issue a key or decide who may embed the
        widget.
      </p>

      <table className="ds-table">
        <caption className="ds-visually-hidden">Team members, active first</caption>
        <thead>
          <tr>
            <th scope="col">Person</th>
            <th scope="col">Role</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="ds-visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {team.data.map((member) => {
            const roleBlock = memberBlock(member, team.data, { action: 'role' });
            const activeBlock = memberBlock(member, team.data, { action: 'deactivate' });
            // Rendered visibly and pointed at by both controls. A disabled
            // button whose reason lives only in a `title` is a button a
            // keyboard user cannot reach the explanation for and a screen
            // reader may never announce — which turns a considered refusal
            // into an interface that appears broken.
            const reasons = [...new Set([roleBlock, activeBlock].filter(Boolean))];
            const reasonId = reasons.length ? `why-${member.id}` : undefined;
            return (
              <tr key={member.id} className={member.is_active ? '' : 'ds-row-inactive'}>
                <td>
                  {member.display_name}
                  <span className="ds-note">{member.email}</span>
                </td>
                <td>
                  {team.can_manage ? (
                    <>
                      <label className="ds-visually-hidden" htmlFor={`role-${member.id}`}>
                        Role for {member.email}
                      </label>
                      <select
                        id={`role-${member.id}`}
                        className="ds-input"
                        value={member.role}
                        disabled={busy || Boolean(roleBlock)}
                        aria-describedby={roleBlock ? reasonId : undefined}
                        onChange={(e) => onChange(member, { role: e.target.value })}
                      >
                        {team.roles.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </>
                  ) : (
                    humanise(member.role)
                  )}
                </td>
                <td>
                  {member.is_active ? 'Active' : 'Deactivated'}
                  {member.is_you ? <span className="ds-note">you</span> : null}
                  {reasons.length ? (
                    <span className="ds-note" id={reasonId}>
                      {reasons.join(' ')}
                    </span>
                  ) : null}
                </td>
                <td className="ds-actions">
                  {team.can_manage && member.is_active ? (
                    <button
                      type="button"
                      className="ds-button"
                      disabled={busy || Boolean(activeBlock)}
                      aria-describedby={activeBlock ? reasonId : undefined}
                      onClick={() => onChange(member, { is_active: false })}
                    >
                      Deactivate
                      <span className="ds-visually-hidden"> {member.email}</span>
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {team.can_manage ? (
        <form className="ds-form" onSubmit={submit} aria-labelledby="add-member">
          <h3 id="add-member" className="ds-h3">
            Add someone
          </h3>
          <div className="ds-row ds-row-2">
            <div className="ds-field">
              <label htmlFor="member-email">Address</label>
              <input
                id="member-email"
                className="ds-input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="ds-field">
              <label htmlFor="member-role">Role</label>
              <select
                id="member-role"
                className="ds-input"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                {team.roles.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error ? (
            <p className="ds-error" role="alert">
              {error}
            </p>
          ) : null}
          <button type="submit" className="ds-button ds-button-primary" disabled={busy || !email}>
            Add
          </button>
        </form>
      ) : null}
    </section>
  );
}

function Retention({ retention }) {
  return (
    <section aria-labelledby="retention-heading" className="ds-settings-block">
      <h2 id="retention-heading" className="ds-h2">
        Retention
      </h2>
      {/* Reported, not configured. A tenant able to shorten its own retention
          below the mandated period would be using a settings screen to fall out
          of compliance, so there is no form here — only the position. */}
      <p className="ds-lede">
        Cases, messages and audit records are kept for {retention.years} years. This is a
        regulatory floor rather than a preference, so it is shown rather than offered.
      </p>

      <dl className="ds-summary">
        <div className="ds-summary-cell">
          <dt>Window</dt>
          <dd className="ds-num ds-figure">{retention.years} years</dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Closed before</dt>
          <dd className="ds-num ds-figure">{retention.cutoff.slice(0, 10)}</dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Cases past the window</dt>
          <dd>
            <span className="ds-num ds-figure">{retention.cases_past_window}</span>
            <span className="ds-help">
              Still present. The sweep reports by default and deletes only when told to, so a case
              past its window has not been forgotten.
            </span>
          </dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Active legal holds</dt>
          <dd>
            <span className="ds-num ds-figure">{retention.active_legal_holds}</span>
            <span className="ds-help">
              Retention and a legal hold point in opposite directions. The hold wins, and held
              material is skipped rather than deleted.
            </span>
          </dd>
        </div>
        <div className="ds-summary-cell">
          <dt>Content sealing</dt>
          <dd>
            <span className="ds-figure">{retention.sealing_enabled ? 'On' : 'Off'}</span>
            <span className="ds-help">
              {retention.sealing_enabled
                ? 'An erasure request can be honoured by destroying the subject key. The audit chain still verifies afterwards.'
                : 'Without it an erasure request can only be refused: an append-only system cannot delete, and nothing here rewrites a record.'}
            </span>
          </dd>
        </div>
      </dl>
    </section>
  );
}

function SignIn() {
  return (
    <section aria-labelledby="signin-heading" className="ds-settings-block">
      <h2 id="signin-heading" className="ds-h2">
        Sign-in
      </h2>
      {/* No SSO form. Nothing in this product implements SAML or OIDC, and a
          settings screen offering a configuration that goes nowhere is worse
          than one that says so — it costs an evaluation, and then a support
          ticket, before anybody finds out. */}
      <p className="ds-lede">
        Agents sign in with a session token issued against an API key. Single sign-on is not
        available in this release — there is no SAML or OIDC support to configure, and this screen
        would rather say so than offer a form that goes nowhere.
      </p>
    </section>
  );
}
