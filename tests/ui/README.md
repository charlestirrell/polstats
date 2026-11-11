# UI smoke test

The explorer has no JavaScript test runner, so `smoke.js` runs inside the page. It drives the real controls
(tabs, search, seat picker, filters, dialogs), compares counts with the REST API where it can, and prints a
pass/fail list.

1. Start the stack with data loaded (see the main README) and open <http://localhost:8000/ui/>. Wait for the page
   and the election data to finish loading.
2. Paste `smoke.js` into the browser's developer console, then run:

   ```js
   await window.__smoke   // -> { passed, total, failed: [...] }
   ```

   Or serve this folder and load it with a script tag:

   ```bash
   python3 -m http.server 8765 --directory tests/ui
   ```

   ```js
   const s = document.createElement('script'); s.src = 'http://localhost:8765/smoke.js'; document.head.append(s);
   ```

It leaves the page in its default state (filters reset, current date). It reads from the loaded dataset, so a few
expectations (for example "100 senators", "TX-12 has a current holder") describe the data as loaded in
November 2026 and will need updating when the data changes.
