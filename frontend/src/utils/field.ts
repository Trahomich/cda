// Общие утилиты полей.
/** Короткий номер поля из названия («7/ВОСХОД/185га» → «7»; на карте —
 *  метка центроида, в календаре — номер в ячейке дня, #192/#763). */
export function shortFieldNumber(name: string): string {
  const head = name.split('/')[0]?.trim()
  return head || name
}
