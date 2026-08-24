/**
 * A picture of the tenant's widget, not a piece of our interface.
 *
 * **This is the one place in the dashboard where a saturated pixel is not about
 * time**, and the exception is deliberate rather than an oversight. DESIGN.md's
 * rule governs *our* chrome; the colour in here belongs to the tenant, and the
 * whole point of the widget is that it looks like their product rather than
 * ours. A preview rendered in ink would be a preview of something that does not
 * exist.
 *
 * Two things keep the exception from leaking. The colour is applied through an
 * inline custom property scoped to this element, so nothing outside it can
 * inherit the tenant's brand; and the preview is drawn inside a visible frame
 * labelled as a preview, so it reads as a picture of another product rather than
 * as part of the console.
 *
 * It is a static rendering, not the real widget in an iframe. Embedding the real
 * one would need a session token minted for a real customer — a live customer
 * session opened to show an engineer a colour.
 */
export default function Preview({ theme, categories }) {
  const style = {
    '--preview-primary': theme.primary_colour || '#0B5FFF',
    '--preview-radius': theme.radius || '8px'
  };

  return (
    <figure className="ds-preview" style={style}>
      <figcaption className="ds-preview-caption">
        Preview — how this renders inside your customer&apos;s page
      </figcaption>

      <div className={`ds-preview-frame ds-preview-${theme.position || 'bottom-right'}`}>
        <div className="ds-preview-page" aria-hidden="true">
          <span className="ds-preview-bar" />
          <span className="ds-preview-bar ds-preview-bar-short" />
          <span className="ds-preview-bar" />
        </div>

        <div className="ds-preview-widget">
          <p className="ds-preview-title">Report a problem</p>
          <p className="ds-preview-hint">Which transaction is this about?</p>
          <ul className="ds-preview-options">
            {(categories.length ? categories : [{ name: 'failed_transfer' }])
              .slice(0, 3)
              .map((category) => (
                <li key={category.name} className="ds-preview-option">
                  {category.name.replace(/_/g, ' ')}
                </li>
              ))}
          </ul>
          <span className="ds-preview-button">Continue</span>
        </div>
      </div>
    </figure>
  );
}
