<script setup lang="ts">
// E-14 «Источники данных» (07-ux §5: руководитель и администратор): состояние
// адаптеров — статус ok/degraded/error, last_success_at, причина и последние
// события журнала dat_source_journal (API-28, REQ-F-034).
import { onMounted, ref } from 'vue'
import { getSourcesStatus } from '@/api'
import type { AdapterState, AdapterStatus } from '@/api/types'

const sources = ref<AdapterStatus[]>([])
const loading = ref(true)
const failed = ref(false)

const STATE_LABELS: Record<AdapterState, string> = {
  ok: 'работает',
  degraded: 'деградация',
  error: 'ошибка',
}

const KIND_LABELS: Record<string, string> = {
  weather: 'погода',
  satellite: 'спутник',
  prices: 'цены',
  telemetry: 'телематика',
  soil: 'агрохимия',
  manual: 'ручной ввод',
}

const fmt = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' })

function fmtAt(iso: string | null): string {
  return iso ? fmt.format(new Date(iso)) : '—'
}

function kindTitle(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

onMounted(async () => {
  try {
    const page = await getSourcesStatus()
    sources.value = page.items
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="src">
    <header class="src__header">
      <h1 class="src__title">
        Источники данных
      </h1>
      <p class="src__sub">
        Состояние адаптеров внешних источников (REQ-F-034). При деградации ядро системы
        продолжает работать, данные помечаются устаревшими.
      </p>
    </header>

    <p
      v-if="loading"
      class="src__state"
    >
      Загрузка состояния источников…
    </p>
    <p
      v-else-if="failed"
      class="src__state src__state--error"
    >
      Не удалось загрузить состояние источников
    </p>

    <div
      v-else
      class="src__grid"
    >
      <article
        v-for="s in sources"
        :key="s.source_id"
        class="src__card"
      >
        <header class="src__card-head">
          <h2 class="src__name">
            {{ s.title }}
          </h2>
          <span
            class="src__badge"
            :class="`src__badge--${s.last_status}`"
          >{{ STATE_LABELS[s.last_status] }}</span>
        </header>
        <dl class="src__meta">
          <div>
            <dt>Идентификатор</dt>
            <dd class="src__mono">{{ s.source_id }}</dd>
          </div>
          <div>
            <dt>Тип</dt>
            <dd>{{ kindTitle(s.kind) }}</dd>
          </div>
          <div>
            <dt>Последний успех</dt>
            <dd>{{ fmtAt(s.last_success_at) }}</dd>
          </div>
        </dl>
        <p
          v-if="s.reason"
          class="src__reason"
        >
          Причина: {{ s.reason }}
        </p>
        <div
          v-if="s.events.length"
          class="src__events"
        >
          <h3 class="src__events-title">
            Последние события
          </h3>
          <ul class="src__events-list">
            <li
              v-for="(e, i) in s.events.slice(0, 3)"
              :key="i"
            >
              <span class="src__mono">{{ fmtAt(e.at) }}</span>
              <span :class="`src__ev--${e.status}`">· {{ STATE_LABELS[e.status] }}</span>
              <span v-if="e.reason">· {{ e.reason }}</span>
            </li>
          </ul>
        </div>
      </article>
      <p
        v-if="sources.length === 0"
        class="src__state"
      >
        Источники не найдены
      </p>
    </div>
  </section>
</template>

<style scoped>
.src {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 72rem;
}

.src__title {
  font-size: 1.35rem;
  margin: 0;
}

.src__sub {
  margin: 0;
  color: #555;
  max-width: 48rem;
}

.src__state {
  color: #555;
}

.src__state--error {
  color: #b3261e;
}

.src__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr));
  gap: 1rem;
}

.src__card {
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: #fff;
}

.src__card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
}

.src__name {
  font-size: 1.05rem;
  margin: 0;
}

.src__badge {
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.9rem;
  font-weight: 600;
}

.src__badge--ok {
  background: #e2f2e4;
  color: #1b5e20;
}

.src__badge--degraded {
  background: #fff3e0;
  color: #a15c00;
}

.src__badge--error {
  background: #fdecea;
  color: #b3261e;
}

.src__meta {
  display: grid;
  gap: 0.25rem;
  margin: 0;
}

.src__meta dt {
  font-size: 0.85rem;
  color: #666;
}

.src__meta dd {
  margin: 0;
  font-weight: 600;
}

.src__mono {
  font-family: ui-monospace, monospace;
  font-size: 0.9rem;
}

.src__reason {
  margin: 0;
  color: #a15c00;
  font-size: 0.95rem;
}

.src__events-title {
  font-size: 0.9rem;
  margin: 0.25rem 0 0;
  color: #444;
}

.src__events-list {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.9rem;
  color: #555;
}

.src__ev--ok {
  color: #1b5e20;
}

.src__ev--degraded {
  color: #a15c00;
}

.src__ev--error {
  color: #b3261e;
}
</style>
