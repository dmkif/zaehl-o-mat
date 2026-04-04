<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <RouterLink :to="`/properties/${propertyId}/meters`" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 text-sm">
        ← {{ $t('meters.title') }}
      </RouterLink>
      <span v-if="meter" class="text-2xl font-bold">{{ meter.name }}</span>
    </div>

    <div v-if="loading" class="text-gray-500">{{ $t('common.loading') }}</div>

    <template v-else-if="meter">
      <!-- Consumption chart -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow p-4 mb-6">
        <h2 class="font-semibold mb-3">{{ $t('meter.consumption') }}</h2>
        <apexchart type="area" height="250" :options="chartOptions" :series="chartSeries" />
      </div>

      <!-- Add reading + OCR -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow p-4 mb-6">
        <h2 class="font-semibold mb-3">{{ $t('meter.add_reading') }}</h2>
        <OcrCapture :meter-id="meterId" :property-id="propertyId" :serial-number="meter.serial_number ?? undefined" @reading-added="fetchReadings" />
      </div>

      <!-- Readings table -->
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow p-4">
        <h2 class="font-semibold mb-3">{{ $t('meter.readings') }}</h2>
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
              <th class="text-left py-2">{{ $t('reading.date') }}</th>
              <th class="text-right py-2">{{ $t('reading.value') }}</th>
              <th class="text-center py-2">{{ $t('reading.source') }}</th>
              <th class="text-center py-2"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in readings" :key="r.id" class="border-b dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30">
              <td class="py-2">{{ formatDate(r.read_at) }}</td>
              <td class="py-2 text-right font-mono">{{ r.value }} {{ meter.unit }}</td>
              <td class="py-2 text-center">
                <span class="text-xs px-2 py-0.5 rounded-full"
                  :class="{
                    'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200': r.source === 'manual',
                    'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200': r.source === 'auto',
                    'bg-gray-100 text-gray-500 dark:bg-gray-700': r.source === 'archived',
                  }"
                >{{ r.source }}</span>
              </td>
              <td class="py-2 text-center">
                <div class="flex items-center justify-center gap-2">
                  <button
                    v-if="r.image_path"
                    class="text-gray-400 hover:text-blue-500 transition-colors"
                    :title="$t('reading.rescan')"
                    @click="startRescan(r)"
                  >
                    <!-- Refresh icon -->
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
                      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                    </svg>
                  </button>
                  <button
                    v-if="authStore.isAdmin"
                    class="text-gray-400 hover:text-yellow-500 transition-colors"
                    :title="$t('reading.edit')"
                    @click="startEdit(r)"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                    </svg>
                  </button>
                  <button
                    v-if="authStore.isAdmin"
                    class="text-gray-400 hover:text-red-500 transition-colors"
                    :title="$t('reading.delete')"
                    @click="confirmDeleteReading(r)"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="3 6 5 6 21 6"/>
                      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                      <path d="M10 11v6M14 11v6"/>
                      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>

  <!-- Re-scan dialog -->
  <div v-if="rescanTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="closeRescan">
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 w-full max-w-md mx-4">
      <h3 class="font-semibold text-lg mb-4">{{ $t('reading.rescan') }}</h3>
      <!-- Image preview -->
      <div class="mb-4 rounded-lg overflow-hidden bg-gray-100 dark:bg-gray-700 flex items-center justify-center min-h-32">
        <img v-if="rescanImageUrl" :src="rescanImageUrl" class="max-w-full max-h-64 object-contain" />
        <span v-else class="text-sm text-gray-400">…</span>
      </div>
      <!-- Engine selector -->
      <div class="flex items-center gap-2 mb-4">
        <label class="text-xs text-gray-500 whitespace-nowrap">{{ $t('ocr.engine_mode') }}</label>
        <select v-model="rescanEngine" class="flex-1 text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5">
          <option value="auto">{{ $t('ocr.engine_auto') }}</option>
          <option value="ocr">{{ $t('ocr.engine_ocr') }}</option>
          <option value="llm">{{ $t('ocr.engine_llm') }}</option>
        </select>
      </div>
      <!-- Result after scan -->
      <template v-if="rescanResult">
        <p class="text-sm text-gray-600 dark:text-gray-300 mb-1">
          {{ $t('reading.detected_value') }}: <span class="font-mono font-semibold">{{ rescanResult.detected_value ?? '—' }}</span>
        </p>
        <p v-if="rescanResult.detected_serial" class="text-sm text-gray-500 dark:text-gray-400 mb-3">
          {{ $t('reading.detected_serial') }}: <span class="font-mono">{{ rescanResult.detected_serial }}</span>
        </p>
      </template>
      <div class="flex gap-3 justify-end mt-2">
        <button class="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600" @click="closeRescan">
          {{ $t('common.close') }}
        </button>
        <button :disabled="rescanScanning" class="px-4 py-2 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold" @click="doRescan">
          {{ rescanScanning ? '…' : $t('ocr.scan') }}
        </button>
      </div>
    </div>
  </div>

  <!-- Edit reading dialog -->
  <div v-if="editTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="editTarget = null">
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 w-full max-w-sm mx-4">
      <h3 class="font-semibold text-lg mb-4">{{ $t('reading.edit_title') }}</h3>
      <div class="space-y-3">
        <div>
          <label class="text-xs text-gray-500">{{ $t('reading.value') }}</label>
          <input v-model="editForm.value" type="number" step="any" class="w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 mt-1" />
        </div>
        <div>
          <label class="text-xs text-gray-500">{{ $t('reading.date') }}</label>
          <input v-model="editForm.read_at" type="datetime-local" class="w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 mt-1" />
        </div>
        <div>
          <label class="text-xs text-gray-500">{{ $t('reading.note') }}</label>
          <input v-model="editForm.note" type="text" class="w-full rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 mt-1" />
        </div>
      </div>
      <div class="flex gap-3 justify-end mt-4">
        <button class="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600" @click="editTarget = null">
          {{ $t('common.cancel') }}
        </button>
        <button class="px-4 py-2 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold" :disabled="editSaving" @click="saveEdit">
          {{ editSaving ? $t('common.saving') : $t('common.save') }}
        </button>
      </div>
    </div>
  </div>

  <!-- Delete confirmation dialog -->
  <div v-if="deleteTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="deleteTarget = null">
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 w-full max-w-sm mx-4">
      <h3 class="font-semibold text-lg mb-2">{{ $t('reading.delete_confirm_title') }}</h3>
      <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">{{ $t('reading.delete_confirm_msg') }}</p>
      <div class="flex gap-3 justify-end">
        <button class="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600" @click="deleteTarget = null">
          {{ $t('common.cancel') }}
        </button>
        <button class="px-4 py-2 text-sm rounded-lg bg-red-600 hover:bg-red-700 text-white font-semibold" :disabled="deleting" @click="doDelete">
          {{ deleting ? '…' : $t('common.delete') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import OcrCapture from '@/components/OcrCapture.vue'

const authStore = useAuthStore()
const route = useRoute()
const { t } = useI18n()
const propertyId = route.params.propertyId as string
const meterId = route.params.meterId as string

const loading = ref(true)
const meter = ref<any>(null)
const readings = ref<any[]>([])

const chartOptions = computed(() => ({
  chart: { toolbar: { show: false }, background: 'transparent' },
  theme: { mode: document.documentElement.classList.contains('dark') ? 'dark' : 'light' },
  xaxis: { type: 'datetime' },
  stroke: { curve: 'smooth' },
  dataLabels: { enabled: false },
  tooltip: { x: { format: 'dd.MM.yyyy HH:mm' } },
}))

const chartSeries = computed(() => {
  if (!readings.value.length) return []
  const data = readings.value.slice().reverse().map((r: any) => ({
    x: new Date(r.read_at).getTime(),
    y: parseFloat(r.value),
  }))
  return [{ name: meter.value?.unit || '', data }]
})

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

onMounted(async () => {
  const [mRes] = await Promise.all([
    apiFetch(`/api/properties/${propertyId}/meters/${meterId}`, {
      headers: { Authorization: `Bearer ${authStore.token}` },
    }),
  ])
  if (mRes.ok) meter.value = await mRes.json()
  await fetchReadings()
  loading.value = false
})

async function fetchReadings() {
  const res = await apiFetch(`/api/properties/${propertyId}/meters/${meterId}/readings?limit=200`, {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) readings.value = await res.json()
}

const rescanResult = ref<any>(null)
const rescanTarget = ref<any>(null)
const rescanEngine = ref<'auto' | 'ocr' | 'llm'>('auto')
const rescanScanning = ref(false)
const rescanImageUrl = ref<string | null>(null)

const editTarget = ref<any>(null)
const editForm = ref({ value: '', read_at: '', note: '' })
const editSaving = ref(false)
const deleteTarget = ref<any>(null)
const deleting = ref(false)

function startEdit(reading: any) {
  editTarget.value = reading
  const dt = new Date(reading.read_at)
  const pad = (n: number) => n.toString().padStart(2, '0')
  const local = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`
  editForm.value = { value: reading.value, read_at: local, note: reading.note ?? '' }
}

async function saveEdit() {
  if (!editTarget.value) return
  editSaving.value = true
  const res = await apiFetch(
    `/api/properties/${propertyId}/meters/${meterId}/readings/${editTarget.value.id}`,
    {
      method: 'PATCH',
      headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        value: parseFloat(editForm.value.value),
        read_at: editForm.value.read_at ? new Date(editForm.value.read_at).toISOString() : undefined,
        note: editForm.value.note || null,
      }),
    },
  )
  editSaving.value = false
  if (res.ok) { editTarget.value = null; await fetchReadings() }
}

function confirmDeleteReading(reading: any) {
  deleteTarget.value = reading
}

async function doDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  const res = await apiFetch(
    `/api/properties/${propertyId}/meters/${meterId}/readings/${deleteTarget.value.id}`,
    { method: 'DELETE', headers: { Authorization: `Bearer ${authStore.token}` } },
  )
  deleting.value = false
  if (res.ok) { deleteTarget.value = null; await fetchReadings() }
}

async function startRescan(reading: any) {
  rescanResult.value = null
  rescanScanning.value = false
  rescanTarget.value = reading
  rescanImageUrl.value = null
  if (reading.image_path) {
    const filename = reading.image_path.split('/').pop()
    const imgRes = await apiFetch(`/api/uploads/${filename}`, {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    if (imgRes.ok) {
      const blob = await imgRes.blob()
      rescanImageUrl.value = URL.createObjectURL(blob)
    }
  }
}

async function doRescan() {
  if (!rescanTarget.value) return
  rescanScanning.value = true
  rescanResult.value = null
  const res = await apiFetch(`/api/ocr/rescan/${rescanTarget.value.id}?engine=${rescanEngine.value}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  rescanScanning.value = false
  if (res.ok) {
    rescanResult.value = await res.json()
  }
}

function closeRescan() {
  if (rescanImageUrl.value) { URL.revokeObjectURL(rescanImageUrl.value); rescanImageUrl.value = null }
  rescanTarget.value = null
  rescanResult.value = null
}
</script>
