// Горизонтальная лента истории севооборота поля (API-49, v1-11 #67):
// ячейка = год с цветом культуры справочника (color_hex, API-48) и кратким
// названием; прошедшие годы — completed, текущий — in_progress (обводка),
// будущие — planned (пунктир, бледнее). Показывает не более max лет, старше —
// «+N», полный список — в title ячейки. Используется в карточке поля (E-03)
// и на экране плана севооборота (E-04).
<script setup lang="ts">
import { computed } from 'vue'
import type { CropCycleHistoryItem } from '@/api/types'

const props = withDefaults(
  defineProps<{
    items: CropCycleHistoryItem[]
    /** Сколько последних лет показывать в ленте (по убыванию года). */
    maxYears?: number
    /** Урожайность по году (ц/га) — в подсказку ячейки (если известна). */
    yields?: Record<number, number>
  }>(),
  { maxYears: 8, yields: () => ({}) },
)

const STATUS_LABELS: Record<string, string> = {
  completed: 'прошедший',
  in_progress: 'текущий',
  planned: 'запланирован',
}

const KIND_LABELS: Record<string, string> = {
  main: '',
  catch: ' · промежуточная',
  fallow: ' · пар',
}

/** Годы по убыванию; в ленту — последние maxYears, скрытые — счётчиком. */
const visible = computed(() => props.items.slice(0, props.maxYears))
const hidden = computed(() => Math.max(0, props.items.length - visible.value.length))

/** Краткое имя культуры для узкой ячейки: первое слово, до 12 символов. */
function shortName(name: string): string {
  const first = name.replace(' (сидерат)', '').split(' ')[0]
  return first.length > 12 ? `${first.slice(0, 11)}…` : first
}

function title(item: CropCycleHistoryItem): string {
  const yld = props.yields[item.year]
  return [
    `${item.year} · ${item.culture_name}${KIND_LABELS[item.kind] ?? ''}`,
    yld ? `${yld} ц/га` : '',
    STATUS_LABELS[item.status] ?? item.status,
  ]
    .filter(Boolean)
    .join(' · ')
}
</script>

<template>
  <div
    v-if="items.length"
    class="strip"
  >
    <span
      v-for="item in visible"
      :key="item.year"
      class="strip__cell"
      :class="`strip__cell--${item.status}`"
      :title="title(item)"
    >
      <span class="strip__year">{{ item.year }}</span>
      <span
        class="strip__swatch"
        :style="{ background: item.color_hex ?? '#9e9e9e' }"
      />
      <span class="strip__name">{{ shortName(item.culture_name) }}</span>
    </span>
    <span
      v-if="hidden > 0"
      class="strip__more"
      :title="`Ещё ${hidden} гг. раньше: ${items
        .slice(maxYears)
        .map((i) => `${i.year} — ${i.culture_name}`)
        .join(', ')}`"
    >+{{ hidden }} …</span>
  </div>
  <p
    v-else
    class="strip__empty"
  >
    Истории севооборота по полю пока нет — заполните клетки плана на экране
    «Севооборот», и лента появится здесь.
  </p>
</template>

<style scoped>
.strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: stretch;
}

.strip__cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.15rem;
  min-width: 4.2rem;
  padding: 0.3rem 0.4rem;
  border: 1px solid #c8e6c9;
  border-radius: 8px;
  background: #fff;
}

/* текущий год — акцентная обводка */
.strip__cell--in_progress {
  border: 2px solid #2e7d32;
}

/* будущие годы — пунктир, бледнее */
.strip__cell--planned {
  border-style: dashed;
  opacity: 0.65;
  background: #fafdf9;
}

.strip__year {
  font-weight: 600;
  font-size: 0.85rem;
  color: #1b5e20;
}

.strip__swatch {
  width: 100%;
  height: 0.4rem;
  border-radius: 3px;
}

.strip__name {
  font-size: 0.8rem;
  color: #424242;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 5.4rem;
  white-space: nowrap;
}

.strip__more {
  display: inline-flex;
  align-items: center;
  padding: 0 0.5rem;
  color: #555;
  font-size: 0.85rem;
}

.strip__empty {
  margin: 0;
  color: #555;
  font-size: 0.9rem;
  max-width: 40rem;
}
</style>
