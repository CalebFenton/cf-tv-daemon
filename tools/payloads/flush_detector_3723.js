// Does the CVE-2022-3723 root cause exist on this engine?
//
// Root cause (Project Zero): in Turbofan, a field's FieldType can decay to
// `None` after the Map backing it is garbage-collected. Turbofan must then avoid
// optimizing stores to that field -- a previous oversight let `kStoreInLiteral`
// proceed anyway. An object rebuilt by a literal store keeps an optimized path
// that believes the old type, and a later store of a different type is not
// deoptimized. That is the type confusion.
//
// The precondition is the interesting part, and it is observable without any
// corruption: a field whose map was collected must produce a fresh map with a
// *different* identity on the next literal store, rather than reusing the dead
// one. If map identity is stable across a collection, the precondition fails and
// the bug cannot be reached.
//
// Map identity is not directly exposed, but it leaks through V8's own inline
// caches: a monomorphic site that has specialised on one map deopts when the
// object's map changes. So timing a warmed function across a rebuild tells us
// whether the map was replaced.
//
// Safe: no OOB, no writes outside ordinary objects.

const out = {trials: [], notes: []};

function forceMajorGC(rounds) {
  let failed = 0;
  for (let i = 0; i < rounds; i++) {
    try { new ArrayBuffer(0x7fe00000); } catch (e) { failed++; }
  }
  return failed;
}

// A shape whose inner property is only ever created by a literal store. That is
// the exact pattern the CVE needs: `kStoreInLiteral` generalises the FieldType,
// and the bug is that it does so even when the FieldType is already None.
function makeObject(payload) {
  return {inner: {value: payload}};
}

// Warm a monomorphic accessor so the site specialises on the current inner map.
function readInner(o) {
  return o.inner.value;
}

function timeCalls(fn, arg, n) {
  const t0 = performance.now();
  let sink = 0;
  for (let i = 0; i < n; i++) sink += (typeof fn(arg) === 'number' ? fn(arg) : 0);
  return {ms: +(performance.now() - t0).toFixed(3), sink};
}

// Trial: warm on a number-valued inner, drop every reference, collect, then
// rebuild with a different type and time the same warmed accessor.
{
  const rec = {name: 'fieldtype-none'};

  // Warm-up: many objects sharing one inner map, all number-valued.
  const warm = [];
  for (let i = 0; i < 2000; i++) warm.push(makeObject(i));
  for (let i = 0; i < 20000; i++) readInner(warm[i % warm.length]);
  rec.warmTiming = timeCalls(readInner, warm[0], 200000);

  // Drop them so the inner map is weakly held and can be collected.
  warm.length = 0;
  rec.failedAllocs = forceMajorGC(6);

  // Rebuild via the same literal-store path with a STRING payload. If the old
  // FieldType survived (the buggy behaviour), an optimized accessor can still
  // assume number. If the fix is present, the map/fieldtype is regenerated and
  // the site deopts cleanly.
  const rebuilt = makeObject('a string now');
  rec.rebuiltType = typeof readInner(rebuilt);
  rec.rebuildTiming = timeCalls(readInner, rebuilt, 200000);

  // A deopt shows up as a timing discontinuity: the first calls after the type
  // change are dramatically slower than the steady state.
  rec.deoptObserved = rec.rebuildTiming.ms > rec.warmTiming.ms * 1.5;
  out.trials.push(rec);
}

// Control: the same accessor on objects that never went through a collection.
// Whatever this does is the baseline "no deopt needed" behaviour.
{
  const rec = {name: 'control-no-gc'};
  const a = makeObject(1);
  for (let i = 0; i < 20000; i++) readInner(a);
  rec.baseline = timeCalls(readInner, a, 200000);
  const b = makeObject('string');
  rec.afterTypeChange = typeof readInner(b);
  rec.typeChangeTiming = timeCalls(readInner, b, 200000);
  out.trials.push(rec);
}

// Whether this engine reuses map identity across a collection. `Object.keys`
// order and the field's descriptor are stable either way, so use a subtler
// signal: creating the same literal shape twice and comparing a proxy-derived
// observation. V8 does not expose maps, so state the limit rather than fake it.
out.mapIdentityObservable = false;
out.note = 'V8 does not expose Map identity to JavaScript; map replacement is '
  + 'inferred from deopt timing, which is a proxy and can be noisy on a 4-core '
  + 'ARMv7 panel. Treat timing alone as weak evidence.';

out.verdict = out.trials.some(t => t.deoptObserved)
  ? 'deopt-after-rebuild-consistent-with-live-fieldtype-transition'
  : 'no-deopt-observed-precondition-unconfirmed';

return JSON.stringify(out, null, 1);
