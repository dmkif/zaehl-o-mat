<template>
  <RouterLink
    :to="`/properties/${meter.property_id}/meters/${meter.meter_id}`"
    class="block bg-white dark:bg-gray-800 rounded-xl shadow p-4 active:scale-95 hover:shadow-md hover:ring-2 hover:ring-brand-200 dark:hover:ring-brand-800 transition-all touch-manipulation select-none"
  >
    <div class="flex items-center justify-between mb-2">
      <span class="font-semibold">{{ meter.meter_name }}</span>
      <div class="flex items-center gap-2">
        <MeterTypeIcon :type="meter.meter_type" />
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </div>
    </div>
    <div class="text-2xl font-bold font-mono">
      {{ meter.latest_value ?? '–' }}
      <span class="text-sm font-normal text-gray-500 dark:text-gray-400 ml-1">{{ meter.unit }}</span>
    </div>
    <div v-if="meter.latest_read_at" class="text-xs text-gray-400 mt-1">
      {{ formatDate(meter.latest_read_at) }}
    </div>
  </RouterLink>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
import MeterTypeIcon from './MeterTypeIcon.vue'

defineProps<{ meter: any }>()

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}
</script>
