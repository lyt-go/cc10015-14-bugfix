<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button v-if="hasDetail" class="link" type="button"
              :data-testid="`detail-${row.id}`" @click="openDetail(row)">查看详情</button>
            <button v-for="action in meta.actions" :key="action" class="link" type="button"
              :data-testid="`action-${row.id}-${action}`" @click="runAction(action, row)">{{ action }}</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">暂无{{ meta.name }}数据，可先登记{{ meta.entity }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="detail" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal">
        <h3>{{ meta.name }}详情 · 记录 {{ detailId }}</h3>
        <div class="detail-block">
          <h4>报告归属 / 传输确认</h4>
          <p v-if="detail['报告归属']">
            报告 {{ detail['报告归属']['报告编号'] ?? '—' }}（ID {{ detail['报告归属']['报告ID'] ?? '—' }}），
            航次 {{ detail['报告归属']['航次编号'] ?? '—' }}，
            接收方 {{ detail['报告归属']['接收方'] ?? '—' }}，
            版本 {{ detail['报告归属']['报告版本'] ?? '—' }}，
            当前轮次 {{ detail['报告归属']['当前轮次'] ?? '—' }}
            <template v-if="detail['报告归属']['可定位'] === false">
              —— 无法定位：{{ detail['报告归属']['说明'] }}
            </template>
          </p>
          <p v-if="detail['传输确认结果']">
            传输状态 {{ detail['传输确认结果']['传输状态'] }}；
            核收结果 {{ detail['传输确认结果']['核收结果'] || '—' }}；
            可继续确认 {{ detail['传输确认结果']['可继续确认'] ? '是' : '否' }}
          </p>
        </div>
        <div v-if="detailRows.length" class="detail-block">
          <h4>成果签收回执</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>回执编号</th><th>报告版本</th><th>轮次</th><th>状态</th>
                <th>核收结果</th><th>引用凭据</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in detailRows" :key="String(item['回执编号'])">
                <td>{{ item['回执编号'] }}</td>
                <td>{{ item['报告版本'] }}</td>
                <td>{{ item['轮次'] }}</td>
                <td>{{ item['状态'] }}</td>
                <td>{{ item['核收结果'] }}</td>
                <td>{{ item['引用凭据'] || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createEntry, downloadEntries, fetchCompileReceipts, fetchTransferConfirmation,
  listEntries, moduleMeta, runAction as applyAction,
} from '@/api/backend-service'
import { fetchJson } from '@/api/client'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const detail = ref<Record<string, any> | null>(null)
const detailId = ref<number | null>(null)
const hasDetail = computed(() =>
  props.moduleKey === 'data_transfer' || props.moduleKey === 'results_compile')
const detailRows = computed<Record<string, any>[]>(() => {
  if (!detail.value) return []
  if (Array.isArray(detail.value['成果签收回执'])) return detail.value['成果签收回执']
  if (Array.isArray(detail.value['签收历史'])) return detail.value['签收历史']
  return []
})

async function openDetail(row: EntryRow) {
  detailId.value = Number(row.id)
  errorMessage.value = ''
  try {
    if (props.moduleKey === 'data_transfer') {
      detail.value = await fetchTransferConfirmation(Number(row.id))
    } else {
      const receipts = await fetchCompileReceipts(Number(row.id))
      const full = await fetchJson<Record<string, any>>(`/api/results_compile/${row.id}`)
      detail.value = { ...full, ...receipts }
    }
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '详情读取失败'
  }
}

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((r) => r.status === meta.statuses[1]).length },
  { label: meta.metrics[2], value: rows.value.filter((r) => r.status === meta.statuses[meta.statuses.length - 1]).length },
])

const statusSummary = computed(() =>
  meta.statuses.map((s) => ({ status: s, count: rows.value.filter((r) => String(r.status) === s).length })),
)

function resetFilters() { filters.value = {}; void reload() }

async function exportRows() {
  errorMessage.value = ''
  try { await downloadEntries(meta.key, filters.value) }
  catch (e) { errorMessage.value = e instanceof Error ? e.message : '导出失败' }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((f) => [f, '']))
  errorMessage.value = ''; showCreate.value = true
}

async function submitCreate() {
  creating.value = true; errorMessage.value = ''
  try {
    const r = await createEntry(meta.key, createValues.value)
    if (!r.ok) throw new Error(r.message)
    showCreate.value = false; await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '登记失败' }
  finally { creating.value = false }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const r = await applyAction(meta.key, Number(row.id), action)
    if (!r.ok) throw new Error(r.message)
    await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '操作失败' }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items; total.value = payload.total
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '业务列表读取失败' }
}

onMounted(reload)
</script>

<style scoped>
.modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.35); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#fff; border-radius:8px; padding:24px; width:480px; max-height:80vh; overflow-y:auto; }
.create-field { display:flex; flex-direction:column; gap:4px; margin-bottom:12px; }
.create-field span { font-size:13px; color:var(--text-secondary,#4a5568); }
.create-field input { padding:6px 8px; border:1px solid var(--border-color,#e2e8f0); border-radius:4px; font-size:13px; }
</style>