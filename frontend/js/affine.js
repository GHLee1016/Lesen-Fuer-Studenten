/** Least-squares 2D affine fit: target ≈ M · [x, y, 1]. Returns a 2×3
 * matrix, or null when the points are degenerate (e.g. collinear). */
export function fitAffine(raw, target) {
  if (raw.length < 3) return null;
  // Normal equations AᵀA θ = Aᵀb, shared AᵀA for both output axes.
  const ata = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  const atx = [0, 0, 0];
  const aty = [0, 0, 0];
  for (let i = 0; i < raw.length; i++) {
    const row = [raw[i][0], raw[i][1], 1];
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) ata[r][c] += row[r] * row[c];
      atx[r] += row[r] * target[i][0];
      aty[r] += row[r] * target[i][1];
    }
  }
  const ax = solve3(ata, atx);
  const ay = solve3(ata, aty);
  return ax && ay ? [ax, ay] : null;
}

function solve3(A, b) {
  const m = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < 3; col++) {
    let piv = col;
    for (let r = col + 1; r < 3; r++) if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
    if (Math.abs(m[piv][col]) < 1e-9) return null;
    [m[col], m[piv]] = [m[piv], m[col]];
    for (let r = 0; r < 3; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c < 4; c++) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, i) => row[3] / row[i]);
}

export function applyAffine(M, x, y) {
  if (!M) return [x, y];
  return [M[0][0] * x + M[0][1] * y + M[0][2], M[1][0] * x + M[1][1] * y + M[1][2]];
}

export function median(values) {
  const v = [...values].sort((a, b) => a - b);
  const n = v.length;
  if (!n) return NaN;
  return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}
