/**
 * Hash routing, in twenty lines, rather than a router dependency.
 *
 * This surface has three routes. Pulling in a router would add more code than
 * the application it routes, and the widget's 4KB discipline is a habit worth
 * keeping on the side of the product nobody measures.
 */
import { useEffect, useState } from 'react';

export function parse(hash) {
  const path = (hash || '').replace(/^#/, '') || '/';
  const caseMatch = path.match(/^\/cases\/([A-Za-z0-9_]+)$/);
  if (caseMatch) return { name: 'case', id: caseMatch[1] };
  if (path === '/reports') return { name: 'reports' };
  return { name: 'queue' };
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}
