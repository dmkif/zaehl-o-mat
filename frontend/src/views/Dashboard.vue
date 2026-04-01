<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">{{ $t('dashboard.title') }}</h1>

    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>

    <template v-else>
      <!-- Stats row -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard :label="$t('dashboard.properties')" :value="summary?.property_count" />
        <StatCard :label="$t('dashboard.meters')" :value="summary?.active_meter_count" />
        <StatCard
          :label="$t('dashboard.oil_price')"
          :value="summary?.oil_market_price?.price_per_100l ? `${summary.oil_market_price.price_per_100l} € / 100L` : '–'"
          :sub="summary?.oil_market_price?.date"
        />
      </div>

      <!-- Meter cards -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <MeterCard
          v-for="m in summary?.meters"
          :key="m.meter_id"
          :meter="m"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import StatCard from '@/components/StatCard.vue'
import MeterCard from '@/components/MeterCard.vue'

const authStore = useAuthStore()
const loading = ref(true)
const summary = ref<any>(null)

onMounted(async () => {
  try {
    const res = await fetch('/api/dashboard/summary', {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    if (res.ok) summary.value = await res.json()
  } finally {
    loading.value = false
  }
})
</script>
