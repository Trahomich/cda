<script setup lang="ts">
// Комбобокс выбора поля с поиском по названию/номеру и блоком «Недавние»
// (фидбек user #391 п.1, задача #134): для хозяйств с десятками полей
// обычный <select> неудобен. Недавние передаются родителем (localStorage).
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { Field } from '@/api/types'

const props = defineProps<{
  fields: Field[]
  modelValue: string | null
  recent?: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const open = ref(false)
const query = ref('')
const root = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)

const selected = computed(() => props.fields.find((f) => f.field_id === props.modelValue) ?? null)

/** Поиск: по названию (без регистра) или по номеру поля в названии. */
function matches(f: Field, q: string): boolean {
  const name = f.name.toLowerCase()
  if (name.includes(q)) return true
  const num = f.name.split('/')[0]
  return q === num
}

const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  const pool = q ? props.fields.filter((f) => matches(f, q)) : props.fields
  if (!q) return pool
  // Точное совпадение номера («45» находит 45/ЖАМ раньше 33/…345га).
  return [...pool].sort((a, b) => Number(b.name.split('/')[0] === q) - Number(a.name.split('/')[0] === q))
})

const recentFields = computed(() => {
  const q = query.value.trim()
  if (q) return []
  const ids = new Set(props.recent ?? [])
  return props.fields.filter((f) => ids.has(f.field_id))
})

function toggle() {
  open.value = !open.value
  if (open.value) {
    query.value = ''
    void nextTick(() => searchInput.value?.focus())
  }
}

function pick(fieldId: string) {
  open.value = false
  if (fieldId !== props.modelValue) emit('update:modelValue', fieldId)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
  if (e.key === 'Enter' && results.value.length) {
    e.preventDefault()
    pick(results.value[0].field_id)
  }
}

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}

watch(open, (v) => {
  if (v) document.addEventListener('mousedown', onDocClick)
  else document.removeEventListener('mousedown', onDocClick)
})

onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))
</script>

<template>
  <div
    ref="root"
    class="fcombo"
  >
    <button
      type="button"
      class="fcombo__btn"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <span class="fcombo__value">{{ selected?.name ?? 'Выберите поле' }}</span>
      <span
        class="fcombo__caret"
        aria-hidden="true"
      >▾</span>
    </button>
    <div
      v-if="open"
      class="fcombo__panel"
      role="listbox"
    >
      <input
        ref="searchInput"
        v-model="query"
        class="fcombo__search"
        type="search"
        placeholder="Поиск: название или номер"
        aria-label="Поиск поля"
        @keydown="onKeydown"
      >
      <template v-if="recentFields.length">
        <p class="fcombo__group">
          Недавние
        </p>
        <ul class="fcombo__list">
          <li
            v-for="f in recentFields"
            :key="f.field_id"
          >
            <button
              type="button"
              class="fcombo__option"
              :class="{ 'fcombo__option--selected': f.field_id === modelValue }"
              role="option"
              :aria-selected="f.field_id === modelValue"
              @click="pick(f.field_id)"
            >
              {{ f.name }}
              <span class="fcombo__meta">{{ f.current_cycle?.culture_name ?? '—' }}</span>
            </button>
          </li>
        </ul>
      </template>
      <p
        v-if="query.trim() && !recentFields.length"
        class="fcombo__group"
      >
        Найдено: {{ results.length }}
      </p>
      <p
        v-else-if="recentFields.length"
        class="fcombo__group"
      >
        Все поля
      </p>
      <ul class="fcombo__list">
        <li
          v-for="f in results"
          :key="f.field_id"
        >
          <button
            type="button"
            class="fcombo__option"
            :class="{ 'fcombo__option--selected': f.field_id === modelValue }"
            role="option"
            :aria-selected="f.field_id === modelValue"
            @click="pick(f.field_id)"
          >
            {{ f.name }}
            <span class="fcombo__meta">{{ f.current_cycle?.culture_name ?? '—' }}</span>
          </button>
        </li>
      </ul>
      <p
        v-if="!results.length"
        class="fcombo__empty"
      >
        Ничего не найдено
      </p>
    </div>
  </div>
</template>

<style scoped>
.fcombo {
  position: relative;
  min-width: 16rem;
}

.fcombo__btn {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  min-height: 44px;
  padding: 0.35rem 0.75rem;
  border: 1px solid #9db59e;
  border-radius: 8px;
  background: #fff;
  font-size: 1rem;
  cursor: pointer;
}

.fcombo__value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fcombo__caret {
  color: #2e7d32;
  font-size: 0.8rem;
}

.fcombo__panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 30;
  background: #fff;
  border: 1px solid #c8d6c9;
  border-radius: 8px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.15);
  padding: 0.4rem;
  max-height: 22rem;
  overflow-y: auto;
}

.fcombo__search {
  width: 100%;
  box-sizing: border-box;
  min-height: 40px;
  padding: 0.25rem 0.6rem;
  border: 1px solid #c8d6c9;
  border-radius: 6px;
  font-size: 1rem;
  margin-bottom: 0.3rem;
}

.fcombo__group {
  margin: 0.35rem 0.2rem 0.15rem;
  font-size: 0.8rem;
  color: #66756a;
  font-weight: 600;
}

.fcombo__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.fcombo__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  padding: 0.45rem 0.6rem;
  border: 0;
  border-radius: 6px;
  background: none;
  font-size: 0.95rem;
  text-align: left;
  cursor: pointer;
}

.fcombo__option:hover {
  background: #eef4ef;
}

.fcombo__option--selected {
  background: #e2efe4;
  font-weight: 600;
}

.fcombo__meta {
  color: #66756a;
  font-size: 0.85rem;
  white-space: nowrap;
}

.fcombo__empty {
  margin: 0.4rem 0.2rem;
  color: #555;
}
</style>
