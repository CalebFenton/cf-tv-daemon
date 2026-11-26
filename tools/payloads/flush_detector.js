// Does the CVE-2022-4262 ROOT CAUSE exist on this engine?
//
// Before tuning any slot indices, establish whether the bug is here at all. The
// root cause is not a memory error in itself: a function's bytecode is discarded
// by a major GC and rebuilt on the next call, and during that rebuild V8's
// parser can decide differently whether a sloppy direct `eval` in this scope may
// introduce variables (`sloppy_eval_can_extend_vars_`). That is a *semantic*
// disagreement between the two compilations.
//
// A semantic disagreement can be observed without any memory corruption:
//
//   function f() {
//     eval("var probe = 42");   // sloppy direct eval
//     return typeof probe;      // 42 if the var extended, undefined if not
//   }
//
// If the flag flips across the flush, `typeof probe` changes across the GC
// boundary. So this probe is safe: no OOB, no map rewriting, nothing that can
// corrupt the heap. It answers "is 4262 reachable here" with a yes/no, which is
// evidence the fragile exploit port cannot give us.
//
// Hypothesis, stated plainly: if this reports no change, it does not prove the
// bug is absent (the parser may reach the same decision through a different
// path). But if it DOES report a change, the root cause is present and the port
// is worth attempting.

const out = {rounds: [], notes: [], hypothesis: 'flag-flip-visible-as-semantic-change'};

// Build the target as source so each round gets fresh bytecode; a cached
// function object would never be re-parsed and the bug could not fire.
function buildTarget() {
  return (0, eval)(`
    (function target() {
      var before = 'pre';
      var readDirect = function () {
        try { return typeof probe + ':' + String(probe); }
        catch (e) { return 'throw:' + e.name; }
      };
      eval("var probe = 42");
      return {before: before, direct: (function () {
        try { return typeof probe + ':' + String(probe); }
        catch (e) { return 'throw:' + e.name; }
      })(), read: readDirect};
    })
  `);
}

// A major collection is what flushes bytecode. Failed oversized allocations are
// the proven trigger on this panel (see gc_weakref.js / the measurements doc).
function forceMajorGC(rounds) {
  let failed = 0;
  for (let i = 0; i < rounds; i++) {
    try { new ArrayBuffer(0x7fe00000); } catch (e) { failed++; }
  }
  return failed;
}

// Collect one observation from a fresh function object.
function observe(label) {
  const fn = buildTarget();
  let first, second;
  try { first = fn(); } catch (e) { first = {error: String(e.message).slice(0, 60)}; }
  try { second = fn(); } catch (e) { second = {error: String(e.message).slice(0, 60)}; }
  return {
    label,
    firstDirect: first && first.direct,
    secondDirect: second && second.direct,
    readFirst: first && first.read ? first.read() : null,
  };
}

// Baseline, no GC in between.
out.rounds.push(observe('baseline'));

// Now the same shape, but with a major collection between the two calls. The
// function object is rebuilt each time, so this exercises fresh bytecode; what
// matters is whether a version compiled AFTER a flush disagrees with one
// compiled before it.
{
  const before = observe('pre-flush');
  const failed = forceMajorGC(6);
  const after = observe('post-flush');
  out.failedAllocs = failed;
  out.rounds.push(before, after);
  out.changed = before.firstDirect !== after.firstDirect;
}

// A second, stronger form: hold a function object across the flush so V8 must
// reuse that exact function's bytecode slot rather than a fresh one. If V8
// discards its bytecode and re-parses on the next call, the scope decision is
// recomputed for the same closure.
{
  const held = buildTarget();
  const firstCall = (() => { try { return held().direct; } catch (e) { return 'err'; } })();
  forceMajorGC(6);
  const secondCall = (() => { try { return held().direct; } catch (e) { return 'err'; } })();
  out.held = {firstCall, secondCall, changed: firstCall !== secondCall};
}

// Third form: many rounds, looking for any round whose result differs from the
// modal value. A single flip is enough to prove the inconsistency exists.
{
  const seen = {};
  for (let i = 0; i < 12; i++) {
    const o = observe('round' + i);
    const key = String(o.firstDirect);
    seen[key] = (seen[key] || 0) + 1;
    if (i % 3 === 2) forceMajorGC(4);
  }
  out.valuesSeen = seen;
  out.multipleValues = Object.keys(seen).length > 1;
}

out.verdict = (out.changed || out.held.changed || out.multipleValues)
  ? 'scope-decision-varies-suggests-root-cause-present'
  : 'no-variation-observed';

return JSON.stringify(out, null, 1);
