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

## Hangs, and why every task now runs in a Worker

A crash and a hang are different failures, and the first version of this harness
only handled the first.

**What happened (2026-11-25).** Task #9 (`cve_2022_4262.js`) ran the reference
exploit's feedback-slot loop on a build whose bytecode layout it was not tuned
for. The result was not a crash: the renderer entered a synchronous loop that
never yielded. The page's own watchdog could not fire, because it runs on the
same single renderer thread it was watching. The Web View died and did **not**
reload. `/health` showed `page_alive: false` and the task sat in `running`
indefinitely.

The lesson: **no in-page timeout can recover from a blocking payload.** Timers,
`setTimeout`, `requestAnimationFrame` and `Promise` races all queue behind the
loop that is stuck. Only something outside the thread can intervene.

**The fix.** Each task now runs in a fresh `Worker`, created from a `Blob` whose
source is built into the page. The page holds the worker handle and a 20 s timer,
and calls `worker.terminate()`, which is the only interruption that works against
a blocking loop. On timeout the task is reported with `timeout: true`, gets the
status `timeout` (distinct from `error` and `crashed`), and the queue moves on.

Verified without a panel, by simulating the client: a `timeout` report sets the
status to `timeout` and clears the running entry.

Two consequences worth knowing:

- A hung payload costs **one deadline**, not the session.
- A `timeout` result is evidence: it means the payload did something to the
  engine that was not a clean exception. Treat it as a signal, not just a
  failure.

**Recovery.** If the page dies, the harness cannot bring it back: the Web View is
driven by a person. `/health` is the signal (`page_alive: false`), and reopening
`http://<host>:8095/` resumes the queue at the persisted cursor.

## Port collisions

Already taken on this host and worth avoiding: `8088`, `8091`, `8096`. A
collision does not look like a collision; it looks like a 404 or a wrong page,
because you are talking to somebody else's server. Check first:

```sh
ss -ltn | grep -E ':(8095|8096) '
```
