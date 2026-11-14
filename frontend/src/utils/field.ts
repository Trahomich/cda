// Общие утилиты полей.
/** Краткий номер поля из названия («7/ВОСХОД/185га» → «7»; на карте —
 *  метка центроида, в календаре — номер в ячейке дня, #192/#763). */
export function shortFieldNumber(name: string): string {
  const head = name.split('/')[0]?.trim()
  return head || name
}

/* ── Палитра типов работ (#790/#203): один источник для календаря
 *  (плашки ячеек, детализация дня, легенда). План — рамка цветом типа,
 *  факт — заливка цветом типа. ── */

export const OP_TYPE_COLORS: Record<string, { bg: string; border: string; label: string }> = {
  seeding: { bg: '#c8e6c9', border: '#2e7d32', label: 'посев' },
  tillage: { bg: '#d7ccc8', border: '#5d4037', label: 'обработка почвы' },
  spraying: { bg: '#e1bee7', border: '#7b1fa2', label: 'опрыскивание' },
  fertilizing: { bg: '#ffe0b2', border: '#ef6c00', label: 'внесение удобрений' },
  seed_treatment: { bg: '#fff9c4', border: '#f9a825', label: 'протравливание семян' },
  harvesting: { bg: '#ffecb3', border: '#ff8f00', label: 'уборка' },
  other: { bg: '#e0e0e0', border: '#757575', label: 'прочее' },
}

/** Цвета типа работы (с запасным «прочее» для неизвестных типов). */
export function opTypeColor(type: string): { bg: string; border: string; label: string } {
  return OP_TYPE_COLORS[type] ?? OP_TYPE_COLORS.other!
}
