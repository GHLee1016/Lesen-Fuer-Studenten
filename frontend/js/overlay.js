/** Canvas renderers for the reading analysis. Coordinates are relative to
 * the article's top-left corner, same as the recorded samples. */

const STOPS = [
  [0.0, [183, 193, 216]],
  [0.35, [47, 79, 147]],
  [0.7, [214, 160, 40]],
  [1.0, [181, 55, 44]],
];

let palette;
function getPalette() {
  if (palette) return palette;
  palette = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < STOPS.length - 2 && t > STOPS[k + 1][0]) k++;
    const [t0, c0] = STOPS[k];
    const [t1, c1] = STOPS[k + 1];
    const f = Math.min(1, Math.max(0, (t - t0) / (t1 - t0)));
    for (let ch = 0; ch < 3; ch++) palette[i * 3 + ch] = c0[ch] + (c1[ch] - c0[ch]) * f;
  }
  return palette;
}

export function sizeCanvas(canvas, w, h) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return ctx;
}

/** Duration-weighted fixation heatmap. */
export function drawHeatmap(ctx, w, h, fixations, radius = 60) {
  if (!fixations.length) return;
  const dpr = window.devicePixelRatio || 1;
  const off = document.createElement("canvas");
  off.width = Math.round(w * dpr);
  off.height = Math.round(h * dpr);
  const o = off.getContext("2d");
  o.scale(dpr, dpr);
  const maxD = Math.max(...fixations.map((f) => f.d));
  for (const f of fixations) {
    const g = o.createRadialGradient(f.x, f.y, 0, f.x, f.y, radius);
    const a = 0.12 + 0.5 * (f.d / maxD);
    g.addColorStop(0, `rgba(0,0,0,${a})`);
    g.addColorStop(1, "rgba(0,0,0,0)");
    o.fillStyle = g;
    o.fillRect(f.x - radius, f.y - radius, radius * 2, radius * 2);
  }
  const img = o.getImageData(0, 0, off.width, off.height);
  const px = img.data;
  let maxA = 1;
  for (let i = 3; i < px.length; i += 4) if (px[i] > maxA) maxA = px[i];
  const pal = getPalette();
  for (let i = 0; i < px.length; i += 4) {
    const a = px[i + 3];
    if (!a) continue;
    const v = Math.round((a / maxA) * 255);
    px[i] = pal[v * 3];
    px[i + 1] = pal[v * 3 + 1];
    px[i + 2] = pal[v * 3 + 2];
    px[i + 3] = Math.min(165, 30 + v * 0.6);
  }
  o.setTransform(1, 0, 0, 1, 0, 0);
  o.putImageData(img, 0, 0);
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(off, 0, 0);
  ctx.restore();
}

/** Scanpath: fixations as circles sized by duration, joined in order. */
export function drawScanpath(ctx, fixations, color = "#2f4f93") {
  if (!fixations.length) return;
  ctx.save();
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.45;
  ctx.beginPath();
  fixations.forEach((f, i) => (i ? ctx.lineTo(f.x, f.y) : ctx.moveTo(f.x, f.y)));
  ctx.stroke();
  const n = fixations.length;
  fixations.forEach((f, i) => {
    const r = Math.min(26, 5 + Math.sqrt(f.d) / 2.2);
    ctx.globalAlpha = 0.25 + 0.45 * (i / Math.max(1, n - 1));
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(f.x, f.y, r, 0, Math.PI * 2);
    ctx.fill();
  });
  // start marker
  const f0 = fixations[0];
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(f0.x, f0.y, 9, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.font = "700 10px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("S", f0.x, f0.y + 0.5);
  ctx.restore();
}
