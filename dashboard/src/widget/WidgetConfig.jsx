import { useState } from 'react';

import { humanise } from '../format.js';
import Preview from './Preview.jsx';
import { brokenCategories, isColour, normaliseOrigin, originProblem } from './config.js';

/**
 * Widget configuration.
 *
 * The screen leads with the cross-check rather than the theming, because the
 * theming is the part that fails visibly. A category offered here with no SLA
 * policy behind it lets a customer choose it and then refuses their filing —
 * and nobody on this side of the product finds out.
 *
 * The broken-category warning is monochrome, and that is a decision rather than
 * an omission: DESIGN.md reserves colour for time, a category with no policy is
 * not a deadline, and stretching the rule a second time would leave the console
 * with two kinds of red meaning two different things. It gets everything else
 * instead — first position, a heavy rule, a label, and the count.
 */
export default function WidgetConfig({ config, canEdit, canChangeOrigins, onSave, onAddOrigin, onRemoveOrigin, busy }) {
  const broken = brokenCategories(config.categories);
  // The draft theme lives here rather than inside the form, because the preview
  // renders from it too. Held in the form, the preview only moved after a save —
  // which would mean publishing a colour to real customers' pages to find out
  // what it looks like, and is not a preview.
  const [draft, setDraft] = useState(() => ({ ...config.theme }));

  return (
    <section aria-labelledby="widget-heading">
      <h1 id="widget-heading" className="ds-h1">
        Widget
      </h1>
      <p className="ds-lede">
        How the widget looks inside your customers&apos; pages, which categories it offers, and
        which origins may embed it.
      </p>

      {broken.length ? (
        <div className="ds-panel ds-broken" role="alert">
          <h2 className="ds-h3">
            {broken.length === 1
              ? '1 category cannot be filed under'
              : `${broken.length} categories cannot be filed under`}
          </h2>
          <p className="ds-note">
            {/* The stored identifier, not a humanised one: the instruction is to
                find it in the editable list below, and that list holds the raw
                names. A friendlier label here costs the operator the minute they
                spend matching one to the other. */}
            <span className="ds-mono">{broken.join(', ')}</span> — offered by the widget with no
            SLA policy behind it. A customer choosing one gets as far as submitting and is then
            told the category is unknown. Add a policy for it, or remove it from the list below.
          </p>
        </div>
      ) : null}

      <div className="ds-widget-layout">
        <Theming
          config={config}
          theme={draft}
          onChange={setDraft}
          canEdit={canEdit}
          onSave={onSave}
          busy={busy}
        />
        <Preview theme={draft} categories={config.categories} />
      </div>

      <Origins
        config={config}
        canEdit={canChangeOrigins}
        onAdd={onAddOrigin}
        onRemove={onRemoveOrigin}
        busy={busy}
      />
    </section>
  );
}

function Theming({ config, theme, onChange, canEdit, onSave, busy }) {
  const setTheme = onChange;
  const [categoryText, setCategoryText] = useState(
    config.categories.map((entry) => entry.name).join('\n')
  );
  const [error, setError] = useState('');

  const colourProblem = isColour(theme.primary_colour)
    ? null
    : 'Not a hex colour. The browser cannot parse this, and your customers would see an unstyled control.';

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      await onSave({
        ...theme,
        categories: categoryText
          .split(/[\n,]+/)
          .map((entry) => entry.trim())
          .filter(Boolean)
      });
    } catch (problem) {
      setError(problem.message);
    }
  }

  return (
    <form className="ds-form ds-widget-form" onSubmit={submit} aria-labelledby="theming-heading">
      <h2 id="theming-heading" className="ds-h2">
        Theme
      </h2>
      <p className="ds-note">
        The widget carries your brand, not ours. Nothing of DisputeShield&apos;s renders inside
        your page.
      </p>

      <div className="ds-row ds-row-2">
        <div className="ds-field">
          <label htmlFor="theme-colour">Primary colour</label>
          <input
            id="theme-colour"
            className="ds-input ds-num"
            value={theme.primary_colour || ''}
            disabled={!canEdit}
            onChange={(e) => setTheme({ ...theme, primary_colour: e.target.value })}
            aria-describedby="theme-colour-help"
            aria-invalid={colourProblem ? 'true' : undefined}
          />
          <p id="theme-colour-help" className="ds-help">
            {colourProblem || 'Hex, for example #0B5FFF.'}
          </p>
        </div>

        <div className="ds-field">
          <label htmlFor="theme-radius">Corner radius</label>
          <input
            id="theme-radius"
            className="ds-input"
            value={theme.radius || ''}
            disabled={!canEdit}
            onChange={(e) => setTheme({ ...theme, radius: e.target.value })}
          />
        </div>

        <div className="ds-field">
          <label htmlFor="theme-position">Position</label>
          <select
            id="theme-position"
            className="ds-input"
            value={theme.position || 'bottom-right'}
            disabled={!canEdit}
            onChange={(e) => setTheme({ ...theme, position: e.target.value })}
          >
            {config.positions.map((position) => (
              <option key={position} value={position}>
                {position.replace(/-/g, ' ')}
              </option>
            ))}
          </select>
        </div>

        <div className="ds-field">
          <label htmlFor="theme-locale">Locale</label>
          <input
            id="theme-locale"
            className="ds-input"
            value={theme.locale || ''}
            disabled={!canEdit}
            onChange={(e) => setTheme({ ...theme, locale: e.target.value })}
          />
        </div>
      </div>

      <div className="ds-field">
        <label htmlFor="theme-categories">Categories offered</label>
        <textarea
          id="theme-categories"
          className="ds-input ds-textarea"
          rows={4}
          value={categoryText}
          disabled={!canEdit}
          onChange={(e) => setCategoryText(e.target.value)}
          aria-describedby="theme-categories-help"
        />
        <p id="theme-categories-help" className="ds-help">
          One per line. Each needs an SLA policy, or a customer who picks it cannot file.
          {config.policies_not_offered.length
            ? ` Policies not offered here: ${config.policies_not_offered.map(humanise).join(', ')} — normal if those arrive by another channel.`
            : ''}
        </p>
      </div>

      {error ? (
        <p className="ds-error" role="alert">
          {error}
        </p>
      ) : null}

      {canEdit ? (
        <button
          type="submit"
          className="ds-button ds-button-primary"
          disabled={busy || Boolean(colourProblem)}
        >
          Save theme
        </button>
      ) : (
        <p className="ds-help">Your role can read this configuration but not change it.</p>
      )}
    </form>
  );
}

function Origins({ config, canEdit, onAdd, onRemove, busy }) {
  const [origin, setOrigin] = useState('');
  const [error, setError] = useState('');
  const problem = origin ? originProblem(normaliseOrigin(origin)) : null;

  async function submit(event) {
    event.preventDefault();
    setError('');
    try {
      await onAdd(normaliseOrigin(origin));
      setOrigin('');
    } catch (issue) {
      setError(issue.message);
    }
  }

  return (
    <section aria-labelledby="origins-heading" className="ds-origins">
      <h2 id="origins-heading" className="ds-h2">
        Embed origins
      </h2>
      <p className="ds-lede">
        The widget renders only on these origins. This is what makes a leaked publishable key
        harmless — it will not load on a page that is not listed here.
      </p>

      <table className="ds-table">
        <caption className="ds-visually-hidden">Origins permitted to embed the widget</caption>
        <thead>
          <tr>
            <th scope="col">Origin</th>
            <th scope="col">
              <span className="ds-visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {config.origins.map((entry) => (
            <tr key={entry.id}>
              <td className="ds-mono">{entry.origin}</td>
              <td className="ds-actions">
                {canEdit ? (
                  <button
                    type="button"
                    className="ds-button"
                    disabled={busy}
                    onClick={() => onRemove(entry)}
                  >
                    Remove
                    <span className="ds-visually-hidden"> {entry.origin}</span>
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
          {config.origins.length === 0 ? (
            <tr>
              <td colSpan={2} className="ds-empty">
                No origins registered. The widget will not render anywhere.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <div className="ds-field">
        <p className="ds-help">
          Sent as <code className="ds-mono">{config.frame_ancestors}</code>. Quoted exactly as the
          browser receives it, because a page that will not load is almost always a page missing
          from this line.
        </p>
      </div>

      {canEdit ? (
        <form className="ds-form" onSubmit={submit} aria-labelledby="add-origin">
          <h3 id="add-origin" className="ds-h3">
            Register an origin
          </h3>
          <div className="ds-field">
            <label htmlFor="origin-value">Origin</label>
            <input
              id="origin-value"
              className="ds-input ds-mono"
              value={origin}
              placeholder="https://app.example.com"
              onChange={(e) => setOrigin(e.target.value)}
              aria-describedby="origin-help"
              aria-invalid={problem ? 'true' : undefined}
            />
            <p id="origin-help" className="ds-help">
              {problem || 'Scheme, host and port. No path, no wildcard.'}
            </p>
          </div>
          {error ? (
            <p className="ds-error" role="alert">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            className="ds-button ds-button-primary"
            disabled={busy || !origin || Boolean(problem)}
          >
            Register
          </button>
        </form>
      ) : (
        <p className="ds-help">
          Only an owner can change this list. It decides who may embed your widget, which is closer
          to an account setting than to a theme.
        </p>
      )}
    </section>
  );
}
