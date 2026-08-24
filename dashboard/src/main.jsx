import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App.jsx';
import { createClient } from './api.js';
import './dashboard.css';

// The API origin is same-origin by default: the dashboard is served by the same
// deployment it talks to. `data-api-base` exists for the development server,
// which runs on its own port.
const root = document.getElementById('root');
const baseUrl = root.dataset.apiBase || window.location.origin;

createRoot(root).render(
  <StrictMode>
    <App client={createClient({ baseUrl })} />
  </StrictMode>
);
