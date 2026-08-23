import { createRoot } from 'react-dom/client';

import App from './App.jsx';
import './widget.css';

const root = document.getElementById('disputeshield-root');
const body = document.body;

createRoot(root).render(
  <App
    publishableKey={body.dataset.key || ''}
    parentOrigin={body.dataset.origin || ''}
    baseUrl={window.location.origin}
  />
);
