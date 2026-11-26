# Family Hub test harness

A task queue the panel's Web View drives itself, so iterating on an exploit does
not need somebody standing at the fridge.

```sh
tools/fridge_harness.py --state .local/harness/state.json --port 8095

# panel: Developer Mode -> Web View -> http://<this-host>:8095/
# leave it open; it polls forever and reconnects on its own

tools/fridge_harness.py --port 8095 enqueue --file tools/payloads/fingerprint.js
tools/fridge_harness.py --port 8095 enqueue 'navigator.userAgent' --label ua
tools/fridge_harness.py --port 8095 tasklist
```

## Why it is built this way

The failure that matters is a **renderer crash**: a wrong V8 payload segfaults
`efl_webprocess`, the page dies, and anything stored only in the page is gone.

- **The queue lives on the server.** State is a JSON file (`--state`), rewritten
  atomically after every claim and report. Restarting the server loses nothing.
- **A dead session's task is re-served.** When a page disappears mid-task, the
  next session to poll gets the same task with `attempts += 1`. After
  `MAX_ATTEMPTS` (3) it is marked `crashed` and the queue moves on, so one bad
  payload cannot wedge the harness.
- **The page persists its cursor in `localStorage` before running a task.** After
  a crash and reload it asks for the task *after* the one that died instead of
  replaying the whole queue.
- **A result is rendered immediately, then POSTed.** The page shows the value the
  moment it is computed, because a later task may kill the renderer before the
  upload completes. The server also prints each result to its own stdout, so
  `proc://fridge-harness` is a live log.
- **Polling is quiet.** The page polls every 400 ms; request logging is off
  unless `HARNESS_VERBOSE=1`.

## Payload convention

A payload file is evaluated as a **statement block**, so it must `return` its
value. A single expression also works (`'1+1'`). The value is JSON-stringified
when possible. On a throw, the stack's first line is reported and the task is
marked `error` — an exception does not kill the page, so an `error` is cheap.

Keep each payload independently runnable: it should not depend on a previous
task's globals, because a crash between them loses that context.

## Ports

`8095` is the default here. Check before changing: `8096` is already taken on
this host by jellyfin, and `8091` by hub-web. A port collision looks like a
mysterious 404 because you are talking to somebody else's server.
