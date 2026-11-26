// Determine the real GPU driver, not the string the browser reports.
//
// The WebGL renderer string said "Mali-450 MP", but this Web View also sends a
// User-Agent claiming Tizen 3.0 on a Tizen 7.2.1 device, so a single string is
// not evidence. What is harder to fake is the capability envelope: a real
// Utgard-era Mali-450 (GL ES 2.0, ~2009 silicon) has a distinctive set of
// limits and a short extension list. A modern Mali, a software rasteriser, or a
// spoofed string each look different.
//
// Why it matters: Mali-450 MP is Utgard, which ships the legacy `mali` driver
// rather than `mali_kbase`. Every public Mali kernel CVE in the last five years
// targets kbase (Midgard/Bifrost/Valhall). If this really is Utgard, those
// exploits do not apply and the Mali escalation family is much weaker than it
// looked. Conversely, if the context turns out to be kbase-class, the CVEs in
// docs/05-firmware/firmware-format.md become relevant again for this device.

const out = {raw: {}, limits: {}, extensions: [], verdict: {}};

const canvas = document.createElement('canvas');
const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
if (!gl) {
  out.verdict.gl = 'no-webgl';
  return JSON.stringify(out, null, 1);
}

const dbg = gl.getExtension('WEBGL_debug_renderer_info');
const param = (name, pname) => {
  try { out.limits[name] = gl.getParameter(pname); }
  catch (e) { out.limits[name] = 'ERR'; }
};

// Identity strings. These come from the driver, though a wrapper can rewrite
// them, so treat each as a hint and the limits below as the evidence.
out.raw.VENDOR = gl.getParameter(gl.VENDOR);
out.raw.RENDERER = gl.getParameter(gl.RENDERER);
out.raw.VERSION = gl.getParameter(gl.VERSION);
out.raw.SHADING_LANGUAGE_VERSION = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
if (dbg) {
  out.raw.UNMASKED_VENDOR = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
  out.raw.UNMASKED_RENDERER = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
}

// The capability envelope. Utgard Mali-400/450 tops out at 4096 textures and has
// a small uniform budget; anything reporting much more is a newer design or a
// different driver entirely.
param('MAX_TEXTURE_SIZE', gl.MAX_TEXTURE_SIZE);
param('MAX_CUBE_MAP_TEXTURE_SIZE', gl.MAX_CUBE_MAP_TEXTURE_SIZE);
param('MAX_RENDERBUFFER_SIZE', gl.MAX_RENDERBUFFER_SIZE);
param('MAX_VIEWPORT_DIMS', gl.MAX_VIEWPORT_DIMS);
param('MAX_VERTEX_ATTRIBS', gl.MAX_VERTEX_ATTRIBS);
param('MAX_VERTEX_UNIFORM_VECTORS', gl.MAX_VERTEX_UNIFORM_VECTORS);
param('MAX_FRAGMENT_UNIFORM_VECTORS', gl.MAX_FRAGMENT_UNIFORM_VECTORS);
param('MAX_VARYING_VECTORS', gl.MAX_VARYING_VECTORS);
param('MAX_TEXTURE_IMAGE_UNITS', gl.MAX_TEXTURE_IMAGE_UNITS);
param('MAX_VERTEX_TEXTURE_IMAGE_UNITS', gl.MAX_VERTEX_TEXTURE_IMAGE_UNITS);
param('MAX_COMBINED_TEXTURE_IMAGE_UNITS', gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS);
param('MAX_VERTEX_OUTPUT_COMPONENTS', gl.MAX_VERTEX_OUTPUT_COMPONENTS);
param('MAX_FRAGMENT_INPUT_COMPONENTS', gl.MAX_FRAGMENT_INPUT_COMPONENTS);
param('MAX_SAMPLES', gl.MAX_SAMPLES);
param('ALIASED_POINT_SIZE_RANGE', gl.ALIASED_POINT_SIZE_RANGE);
param('ALIASED_LINE_WIDTH_RANGE', gl.ALIASED_LINE_WIDTH_RANGE);
param('RED_BITS', gl.RED_BITS);
param('DEPTH_BITS', gl.DEPTH_BITS);
param('STENCIL_BITS', gl.STENCIL_BITS);

out.extensions = (gl.getSupportedExtensions() || []).sort();

// Compile a shader that is legal ES 2.0 but rejected by some stripped
// implementations, to see whether this is a full pipeline or a shim.
const shader = gl.createShader(gl.VERTEX_SHADER);
gl.shaderSource(shader, 'attribute vec4 p; void main(){ gl_Position = p; }');
gl.compileShader(shader);
out.shaderCompiled = gl.getShaderParameter(shader, gl.COMPILE_STATUS);
out.shaderLog = (gl.getShaderInfoLog(shader) || '').slice(0, 200);

// Renderability: a context that cannot draw is a stub.
out.verdict.drawingBuffer = [gl.drawingBufferWidth, gl.drawingBufferHeight];

// Classify without overclaiming.
const ext = new Set(out.extensions);
const maxTex = Number(out.limits.MAX_TEXTURE_SIZE);
if (out.raw.VERSION && /WebGL 1/.test(out.raw.VERSION) === false
    && String(out.raw.VERSION).indexOf('WebGL') === -1) {
  out.verdict.versionString = 'non-standard';
}
out.verdict.classification =
  !out.shaderCompiled ? 'stub-or-broken'
  : ext.has('WEBGL_draw_buffers') && ext.has('OES_texture_float')
    ? 'es3-capable-or-backported'
  : maxTex <= 4096 && ext.size < 40
    ? 'consistent-with-utgard'
  : 'unclassified';

out.verdict.note = 'classification is a heuristic from limits and extensions; '
  + 'confirm against /dev/mali0 or the loaded module when a shell is available';

return JSON.stringify(out, null, 1);
