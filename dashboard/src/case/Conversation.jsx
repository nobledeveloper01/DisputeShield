import { formatWhen, humanise } from '../format.js';

/**
 * The conversation, and the internal notes inside it.
 *
 * §10 guarantees structurally that an internal note cannot leak to a customer —
 * the widget serializer has no field path to one. This component exists for the
 * other half of that problem, which no serializer can solve: **an agent
 * believing they wrote one thing when they wrote another.**
 *
 * DESIGN.md asks for four separate signals, and all four are here because any
 * one of them alone fails somebody: a different background (fails a screen
 * reader), a different alignment (fails at narrow widths), a persistent label
 * (the one that always works), and a distinct composer.
 */
export default function Conversation({ messages }) {
  return (
    <ol className="ds-conversation">
      {messages.map((message) => {
        const internal = message.visibility === 'internal';
        return (
          <li
            key={message.id}
            className={`ds-message ${internal ? 'ds-message-internal' : 'ds-message-customer'}`}
          >
            <p className="ds-message-meta">
              <span className="ds-message-tag">
                {internal ? 'Internal note — not visible to the customer' : 'Customer-visible'}
              </span>
              <span className="ds-note">
                {humanise(message.author_type)} ·{' '}
                {/* The machine-readable value stays in `dateTime`; the text is
                    for the person reading the thread. */}
                <time dateTime={message.created_at}>{formatWhen(message.created_at)}</time>
              </span>
            </p>
            <p className="ds-message-body">{message.body}</p>
          </li>
        );
      })}
      {messages.length === 0 ? <li className="ds-empty">No messages yet.</li> : null}
    </ol>
  );
}
