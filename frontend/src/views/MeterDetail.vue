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
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import OcrCapture from '@/components/OcrCapture.vue'

const authStore = useAuthStore()
const route = useRoute()
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
</script>
