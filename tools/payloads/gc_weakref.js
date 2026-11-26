// Detect a real garbage collection. Corrected.
//
// Two earlier versions of this probe were wrong, both for instructive reasons:
//
//  1. Wrapping the tracked object in an IIFE let V8 scalar-replace it, so it was
//     never a heap allocation and could never be collected.
//  2. Even with a real allocation, the check ran in the same job. By spec
//     (WeakRef "KeepDuringJob"), a WeakRef's target is kept alive until the
//     current job ends, so a synchronous check ALWAYS reports "still alive".
//     Verified in Node: the same object reads alive sync, and dead after one
//     macrotask turn plus gc().
//
// So this probe drops the strong reference, yields a macrotask, then checks.
//
// Why it matters: CVE-2022-4262 requires bytecode flushing, which happens only
// during a major (mark-compact) GC. If this Web View never completes one, the
// bug is unreachable regardless of engine version.

const out = {trials: [], notes: []};

const tick = (ms) => new Promise(r => setTimeout(r, ms || 0));

// Allocate off the stack path: a function boundary plus a random field means the
// object is a real heap allocation, not a scalar replacement.
function makeTarget() {
  return JSON.parse('{"k":' + Math.floor(Math.random() * 1e9) + '}');
}

function churn(rounds) {
  const junk = [];
  for (let r = 0; r < rounds; r++) {
    for (let i = 0; i < 50000; i++) junk.push({i, a: [i, i + 1], s: 'j' + i});
  }
  return junk.length;   // caller drops it
}

const finalized = [];
const registry = typeof FinalizationRegistry === 'function'
  ? new FinalizationRegistry(t => finalized.push(t)) : null;

// One trial: hold, pressure, drop, yield, observe.
async function trial(name, pressure) {
  let strong = makeTarget();
  const ref = new WeakRef(strong);
  if (registry) registry.register(strong, name);

  let allocated = 0;
  try { allocated = pressure(); } catch (e) { out.notes.push(name + ': ' + e.message); }

  strong = null;            // drop the only strong reference
  await tick(0);            // let KeepDuringJob expire

  // More pressure AFTER the drop, so a collection has a reason to happen while
  // nothing references the target.
  churn(2);
  if (typeof globalThis.gc === 'function') globalThis.gc();
  await tick(0);

  const alive = ref.deref() !== undefined;
  out.trials.push({name, observed: alive ? 'alive' : 'collected', churned: allocated});
  return !alive;
}

// Pressure source 1: failed oversized allocations, which is what the reference
// exploit uses.
await trial('failed-oversize', () => {
  let n = 0;
  for (let i = 0; i < 6; i++) { try { new ArrayBuffer(0x7fe00000); n++; } catch (e) {} }
  return n;
});

// Pressure source 2: one large allocation, then release it. This panel allowed
// a 1 GiB buffer earlier, so it is available here.
await trial('large-then-free', () => {
  const big = new ArrayBuffer(0x40000000);
  return big.byteLength;
});

// Pressure source 3: pure JS churn with no external allocation at all.
await trial('js-churn', () => churn(6));

// Let any FinalizationRegistry callbacks run.
await tick(50);

out.finalizedCount = finalized.length;
out.hasWeakRef = typeof WeakRef;
out.hasFinalizationRegistry = typeof FinalizationRegistry;
out.explicitGc = [typeof globalThis.gc, typeof globalThis.CollectGarbage];

const collected = out.trials.some(t => t.observed === 'collected');
const explicit = out.explicitGc[0] === 'function' || out.explicitGc[1] === 'function';
out.verdict = collected ? 'collection-observed'
  : out.finalizedCount > 0 ? 'finalization-observed'
  : explicit ? 'explicit-gc-only'
  : 'no-collection-observed';

return JSON.stringify(out, null, 1);
