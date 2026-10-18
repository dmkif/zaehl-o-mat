<template>
  <div>
    <div class="flex items-center gap-3 mb-6 flex-wrap">
      <RouterLink :to="`/properties/${propertyId}/meters`" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 text-sm">
        ← {{ $t('oil.back') }}
      </RouterLink>
      <span class="text-gray-400">/</span>
      <h1 class="text-2xl font-bold">{{ $t('oil.title') }}</h1>
      <button
        v-if="canWrite"
        @click="openCreate()"
        class="ml-auto px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-sm font-medium transition"
      >
        + {{ $t('oil.add') }}
      </button>
    </div>

    <p v-if="pageError" class="mb-4 text-sm text-red-600 dark:text-red-400">{{ pageError }}</p>
    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>
    <div v-else-if="!deliveries.length && !pageError" class="text-gray-500 dark:text-gray-400">{{ $t('oil.empty') }}</div>

    <div v-else class="flex flex-col gap-3">
      <div
        v-for="d in deliveries"
        :key="d.id"
        class="p-4 rounded-xl border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm flex items-start justify-between gap-3"
      >
        <div class="min-w-0">
          <div class="font-semibold">{{ formatDateTime(d.delivered_at) }}</div>
          <div class="text-sm mt-1">
            <span class="font-mono">{{ formatNumber(d.liters, 0) }} L</span>
            <span v-if="d.total_price != null" class="text-gray-500 dark:text-gray-400"> · {{ formatNumber(d.total_price, 2) }} €</span>
            <span v-if="d.price_per_liter != null" class="text-gray-500 dark:text-gray-400"> · {{ formatNumber(d.price_per_liter, 4) }} €/L</span>
          </div>
          <div v-if="d.supplier" class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ d.supplier }}</div>
          <div v-if="d.note" class="text-sm text-gray-500 dark:text-gray-400 mt-1 whitespace-pre-line break-words">{{ d.note }}</div>
        </div>
        <div class="flex gap-1 shrink-0">
          <button
            v-if="canWrite"
            class="text-gray-400 hover:text-yellow-500 transition-colors p-1"
            :title="$t('oil.edit')"
            @click="openEdit(d)"
          >✎</button>
          <button
            v-if="canDelete"
            class="text-gray-400 hover:text-red-500 transition-colors p-1"
            :title="$t('common.delete')"
            @click="deleteTarget = d"
          >🗑</button>
        </div>
      </div>
    </div>

    <!-- Create / edit modal -->
    <Teleport to="body">
      <div v-if="showForm" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="showForm = false">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl max-h-[90vh] overflow-y-auto">
          <h2 class="text-xl font-bold mb-4">{{ editId ? $t('oil.edit') : $t('oil.add') }}</h2>
          <form @submit.prevent="save" class="flex flex-col gap-3">
            <label class="text-sm">{{ $t('oil.delivered_at') }}
              <input v-model="form.delivered_at" type="datetime-local" required
                class="mt-1 w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            </label>
            <label class="text-sm">{{ $t('oil.liters') }}
              <input v-model="form.liters" inputmode="decimal" required
                class="mt-1 w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            </label>
            <label class="text-sm">{{ $t('oil.total_price') }}
              <input v-model="form.total_price" inputmode="decimal"
                class="mt-1 w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            </label>
            <label class="text-sm">{{ $t('oil.price_per_liter') }}
              <input v-model="form.price_per_liter" inputmode="decimal"
                class="mt-1 w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            </label>
            <p class="text-xs text-gray-500 dark:text-gray-400">{{ $t('oil.price_hint') }}</p>
            <label class="text-sm">{{ $t('oil.supplier') }}
              <input v-model="form.supplier" maxlength="200"
                class="mt-1 w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            </label>
            <label class="text-sm">{{ $t('oil.note') }}
              <textarea v-model="form.note" rows="2" maxlength="2000"
                class="mt-1 w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            </label>
            <p v-if="formError" class="text-sm text-red-600 dark:text-red-400" role="alert">{{ formError }}</p>
            <div class="flex gap-2 justify-end mt-2">
              <button type="button" @click="showForm = false" class="px-4 py-2 rounded-lg border dark:border-gray-600">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" :disabled="saving" class="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium">
                {{ saving ? $t('common.saving') : $t('common.save') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- Delete confirmation -->
    <Teleport to="body">
      <div v-if="deleteTarget" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="deleteTarget = null">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-2">{{ $t('oil.delete_title') }}</h2>
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-2">
            {{ formatDateTime(deleteTarget.delivered_at) }} · {{ formatNumber(deleteTarget.liters, 0) }} L
          </p>
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">{{ $t('oil.delete_msg') }}</p>
          <p v-if="deleteError" class="text-sm text-red-600 dark:text-red-400 mb-2" role="alert">{{ deleteError }}</p>
          <div class="flex gap-2 justify-end">
            <button @click="deleteTarget = null" class="px-4 py-2 rounded-lg border dark:border-gray-600">
              {{ $t('common.cancel') }}
            </button>
            <button :disabled="deleting" @click="doDelete" class="px-4 py-2 bg-red-600 text-white rounded-lg font-medium">
              {{ deleting ? '…' : $t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import { buildDeliveryPayload, formatApiError, formatInputNumber, toLocalInput, type DeliveryForm } from '@/utils/oilDeliveries'
import { usePropertyPerms } from '@/utils/usePropertyPerms'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const propertyId = route.params.propertyId as string
const base = `/api/properties/${encodeURIComponent(propertyId)}/oil-deliveries`
const { canWrite, canDelete, loadRole } = usePropertyPerms(propertyId)

const loading = ref(true)
const pageError = ref('')
const deliveries = ref<any[]>([])

const showForm = ref(false)
const editId = ref<string | null>(null)
const saving = ref(false)
const formError = ref('')
const emptyForm = (): DeliveryForm => ({ delivered_at: '', liters: '', total_price: '', price_per_liter: '', supplier: '', note: '' })
const form = ref<DeliveryForm>(emptyForm())

const deleteTarget = ref<any>(null)
const deleting = ref(false)
const deleteError = ref('')

function headers(json = false): Record<string, string> {
  const h: Record<string, string> = { Authorization: `Bearer ${authStore.token}` }
  if (json) h['Content-Type'] = 'application/json'
  return h
}

function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
}

function formatNumber(v: number | string | null | undefined, digits: number): string {
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('de-DE', { maximumFractionDigits: digits }) : ''
}

async function errorFrom(res: Response, fallbackKey: string): Promise<string> {
  let detail: unknown = null
  try {
    detail = (await res.json())?.detail
  } catch { /* no JSON body */ }
  return formatApiError(detail, t(fallbackKey))
}

async function fetchDeliveries() {
  loading.value = true
  pageError.value = ''
  try {
    const res = await apiFetch(base, { headers: headers() })
    if (res.ok) deliveries.value = await res.json()
    else pageError.value = await errorFrom(res, 'oil.load_error')
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : t('oil.load_error')
  } finally {
    loading.value = false
  }
}

function openCreate(prefill?: Partial<DeliveryForm>) {
  editId.value = null
  form.value = { ...emptyForm(), ...prefill }
  formError.value = ''
  showForm.value = true
}

function openEdit(d: any) {
  editId.value = d.id
  form.value = {
    delivered_at: toLocalInput(d.delivered_at),
    liters: formatInputNumber(d.liters),
    total_price: formatInputNumber(d.total_price),
    price_per_liter: formatInputNumber(d.price_per_liter),
    supplier: d.supplier ?? '',
    note: d.note ?? '',
  }
  formError.value = ''
  showForm.value = true
}

async function save() {
  const r = buildDeliveryPayload(form.value)
  if (!r.ok) {
    formError.value = t(`oil.err_${r.error}`)
    return
  }
  saving.value = true
  formError.value = ''
  try {
    const res = await apiFetch(editId.value ? `${base}/${encodeURIComponent(editId.value)}` : base, {
      method: editId.value ? 'PATCH' : 'POST',
      headers: headers(true),
      body: JSON.stringify(r.payload),
    })
    if (res.ok) {
      showForm.value = false
      await fetchDeliveries()
    } else {
      formError.value = await errorFrom(res, 'oil.save_error')
    }
  } catch (e) {
    formError.value = e instanceof Error ? e.message : t('oil.save_error')
  } finally {
    saving.value = false
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    const res = await apiFetch(`${base}/${encodeURIComponent(deleteTarget.value.id)}`, { method: 'DELETE', headers: headers() })
    if (res.ok) {
      deleteTarget.value = null
      await fetchDeliveries()
    } else {
      deleteError.value = await errorFrom(res, 'oil.delete_error')
    }
  } catch (e) {
    deleteError.value = e instanceof Error ? e.message : t('oil.delete_error')
  } finally {
    deleting.value = false
  }
}

onMounted(async () => {
  await Promise.all([fetchDeliveries(), loadRole()])
  // Pre-fill from the dashboard's "probable delivery" notice (?prefill_at=<iso>&prefill_liters=<n>)
  const at = route.query.prefill_at
  const liters = route.query.prefill_liters
  if (canWrite.value && typeof at === 'string') {
    const n = typeof liters === 'string' ? Number(liters) : NaN
    openCreate({
      delivered_at: toLocalInput(at),
      liters: Number.isFinite(n) ? String(Math.round(n)) : '',
    })
    router.replace({ query: {} })
  }
})
</script>
