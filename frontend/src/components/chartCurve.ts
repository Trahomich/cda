// Утилита плавных кривых для SVG-графиков (полировка погоды #149 п.3):
// Catmull-Rom → кубический Безье. Кривая проходит ровно через точки данных,
// а контрольные точки зажаты в вертикальный диапазон своего сегмента, поэтому
// выбросов за min/max соседних точек нет — данные не искажаются.
export interface CurvePoint {
  x: number
  y: number
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const f = (v: number) => v.toFixed(1)

/** Путь «M … C …» через все точки; 0–1 точка — вырожденный путь. */
export function smoothPath(pts: CurvePoint[]): string {
  if (pts.length === 0) return ''
  if (pts.length === 1) return `M ${f(pts[0]!.x)} ${f(pts[0]!.y)}`
  let d = `M ${f(pts[0]!.x)} ${f(pts[0]!.y)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i]!
    const p1 = pts[i]!
    const p2 = pts[i + 1]!
    const p3 = pts[i + 2] ?? p2
    const yLo = Math.min(p1.y, p2.y)
    const yHi = Math.max(p1.y, p2.y)
    const c1x = p1.x + (p2.x - p0.x) / 6
    const c1y = clamp(p1.y + (p2.y - p0.y) / 6, yLo, yHi)
    const c2x = p2.x - (p3.x - p1.x) / 6
    const c2y = clamp(p2.y - (p3.y - p1.y) / 6, yLo, yHi)
    d += ` C ${f(c1x)} ${f(c1y)} ${f(c2x)} ${f(c2y)} ${f(p2.x)} ${f(p2.y)}`
  }
  return d
}

/** Разбить ряд на отрезки непрерывных данных: null разрывает линию,
 *  чтобы пропущенные сутки не соединялись прямой через пропуск. */
export function continuousSegments<T>(
  items: T[],
  value: (item: T, index: number) => number | null,
  x: (index: number) => number,
  y: (v: number) => number,
): CurvePoint[][] {
  const out: CurvePoint[][] = []
  let cur: CurvePoint[] = []
  items.forEach((item, i) => {
    const v = value(item, i)
    if (v == null) {
      if (cur.length) out.push(cur)
      cur = []
    } else {
      cur.push({ x: x(i), y: y(v) })
    }
  })
  if (cur.length) out.push(cur)
  return out
}
