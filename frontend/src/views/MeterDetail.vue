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
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>

  <!-- Re-scan result dialog -->
  <div v-if="rescanResult" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="rescanResult = null">
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 w-full max-w-sm mx-4">
      <h3 class="font-semibold text-lg mb-4">{{ $t('reading.rescan_result') }}</h3>
      <!-- Engine selector -->
      <div class="flex items-center gap-2 mb-4">
        <label class="text-xs text-gray-500 whitespace-nowrap">{{ $t('ocr.engine_mode') }}</label>
        <select v-model="rescanEngine" class="flex-1 text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5">
          <option value="auto">{{ $t('ocr.engine_auto') }}</option>
          <option value="ocr">{{ $t('ocr.engine_ocr') }}</option>
          <option value="llm">{{ $t('ocr.engine_llm') }}</option>
        </select>
      </div>
      <p class="text-sm text-gray-600 dark:text-gray-300 mb-1">
        {{ $t('reading.detected_value') }}: <span class="font-mono font-semibold">{{ rescanResult.detected_value ?? '—' }}</span>
      </p>
      <p v-if="rescanResult.detected_serial" class="text-sm text-gray-500 dark:text-gray-400 mb-4">
        {{ $t('reading.detected_serial') }}: <span class="font-mono">{{ rescanResult.detected_serial }}</span>
      </p>
      <div class="flex gap-3 justify-end">
        <button class="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600" @click="rescanResult = null">
          {{ $t('common.close') }}
        </button>
        <button class="px-4 py-2 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold" @click="retryRescan">
          {{ $t('ocr.scan') }}
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

async function startRescan(reading: any) {
  rescanResult.value = null
  rescanTarget.value = reading
  const res = await apiFetch(`/api/ocr/rescan/${reading.id}?engine=${rescanEngine.value}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) {
    rescanResult.value = await res.json()
  }
}

async function retryRescan() {
  if (!rescanTarget.value) return
  rescanResult.value = null
  await startRescan(rescanTarget.value)
}
</script>
