// Does a failed huge allocation actually force a mark-compact GC?
//
// The CVE-2022-4262 exploit depends on `new ArrayBuffer(0x7fe00000)` triggering
// a major GC so the target function's bytecode is flushed. On this panel that
// allocation FAILS (heap limit 347 MB), and the reference exploit explicitly
// says the failure does not matter: the *attempt* is what provokes the GC.
//
// That claim has to be verified here, because if a failed allocation does not
// collect, bytecode is never flushed and the whole bug never triggers.
//
// Detection without `%CollectGarbage`: a mark-compact pass drops usedJSHeapSize
// and moves surviving objects, so a large live set that shrinks is evidence.
// Runs as a statement block; must `return`.

const mem = performance.memory;
const out = { heap: {}, samples: [] };

const usedMB = () => (performance.memory
  ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null);
const totalMB = () => (performance.memory
  ? +(performance.memory.totalJSHeapSize / 1048576).toFixed(1) : null);

out.heap.beforeLimitMB = mem ? Math.round(performance.memory.jsHeapSizeLimit / 1048576) : null;
out.heap.beforeUsedMB = usedMB();

// Create a lot of collectable garbage, then hold a known live set so a
// compaction has something to move.
const live = [];
for (let i = 0; i < 20000; i++) live.push({i, s: 'x' + i});
const garbage = [];
for (let i = 0; i < 50000; i++) garbage.push([i, i * 2, 'g' + i]);

out.heap.afterAllocUsedMB = usedMB();

// The exploit's GC trigger: an allocation far beyond the heap limit.
let failed = 0, threw = 0;
for (let i = 0; i < 6; i++) {
  const before = usedMB();
  try {
    const b = new ArrayBuffer(0x7fe00000);
    out.samples.push({i, ok: true, size: b.byteLength, usedBefore: before, usedAfter: usedMB()});
  } catch (e) {
    threw++;
    out.samples.push({i, ok: false, err: String(e.message).slice(0, 40),
                      usedBefore: before, usedAfter: usedMB()});
  }
}
out.failedAllocsThrew = threw;
out.heap.afterFlushUsedMB = usedMB();
out.heap.afterFlushTotalMB = totalMB();

// A second, differently-sized overshoot: some engines size a "huge" allocation
// differently and take a different GC path.
try { new ArrayBuffer(0x40000000); out.gcVia1GB = 'allocated'; }
catch (e) { out.gcVia1GB = 'threw'; }

out.heap.finalUsedMB = usedMB();
out.liveIntact = live.length === 20000 && live[19999].s === 'x19999';

// If used heap fell after the failed allocations, a collection ran.
out.collectionHappened =
  out.heap.afterAllocUsedMB !== null && out.heap.afterFlushUsedMB !== null
    ? out.heap.afterFlushUsedMB < out.heap.afterAllocUsedMB
    : 'unknown';

return JSON.stringify(out, null, 1);
