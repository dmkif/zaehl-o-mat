<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <RouterLink :to="`/properties`" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 text-sm">
        ← {{ $t('properties.title') }}
      </RouterLink>
      <span class="text-gray-400">/</span>
      <h1 class="text-2xl font-bold">{{ $t('meters.title') }}</h1>
      <div class="ml-auto">
        <button
          v-if="authStore.isManager"
          @click="showBulkImport = true"
          class="flex items-center gap-2 px-3 py-1.5 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-medium transition"
        >
          📦 {{ $t('bulk.title') }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="m in meters"
        :key="m.id"
        class="relative block p-4 rounded-xl border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm hover:shadow transition"
      >
        <RouterLink :to="`/properties/${propertyId}/meters/${m.id}`" class="block">
          <div class="flex items-center justify-between pr-14">
            <span class="font-semibold">{{ m.name }}</span>
            <MeterTypeIcon :type="m.meter_type" />
          </div>
          <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ m.unit }} · {{ m.serial_number }}</div>
          <div class="text-xs text-gray-400 mt-1">{{ m.location }}</div>
        </RouterLink>
        <div v-if="authStore.isManager" class="absolute top-3 right-3 flex gap-1">
          <button
            class="text-gray-400 hover:text-yellow-500 transition-colors p-1"
            :title="$t('meters.edit')"
            @click.prevent="startEditMeter(m)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </button>
          <button
            v-if="authStore.isAdmin"
            class="text-gray-400 hover:text-red-500 transition-colors p-1"
            :title="$t('meters.delete')"
            @click.prevent="confirmDeleteMeter(m)"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
            </svg>
          </button>
        </div>
      </div>

      <button
        v-if="authStore.isManager"
        @click="showCreate = true"
        class="p-4 rounded-xl border-2 border-dashed dark:border-gray-600 flex items-center justify-center text-gray-400 hover:text-brand-600 hover:border-brand-400 transition"
      >
        + {{ $t('meters.add') }}
      </button>
    </div>

    <!-- Create meter modal -->
    <Teleport to="body">
      <div v-if="showCreate" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-4">{{ $t('meters.add') }}</h2>
          <form @submit.prevent="createMeter" class="flex flex-col gap-3">
            <input v-model="newMeter.name" :placeholder="$t('meters.name')" required
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <select v-model="newMeter.meter_type"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="water">💧 {{ $t('meter_types.water') }}</option>
              <option value="electricity">⚡ {{ $t('meter_types.electricity') }}</option>
              <option value="oil">🛢️ {{ $t('meter_types.oil') }}</option>
            </select>
            <select v-model="newMeter.unit"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="m³">m³</option>
              <option value="kWh">kWh</option>
              <option value="L">Liter</option>
            </select>
            <input v-model="newMeter.serial_number" :placeholder="$t('meters.serial')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="newMeter.location" :placeholder="$t('meters.location')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <div class="flex gap-2 justify-end mt-2">
              <button type="button" @click="showCreate = false" class="px-4 py-2 rounded-lg border dark:border-gray-600">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" class="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium">
                {{ $t('common.save') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- Edit meter modal -->
    <Teleport to="body">
      <div v-if="editTarget" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="editTarget = null">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-4">{{ $t('meters.edit_title') }}</h2>
          <form @submit.prevent="saveEditMeter" class="flex flex-col gap-3">
            <input v-model="editForm.name" :placeholder="$t('meters.name')" required
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="editForm.serial_number" :placeholder="$t('meters.serial')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="editForm.location" :placeholder="$t('meters.location')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <div class="flex gap-2 justify-end mt-2">
              <button type="button" @click="editTarget = null" class="px-4 py-2 rounded-lg border dark:border-gray-600">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" :disabled="editSaving" class="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium">
                {{ editSaving ? $t('common.saving') : $t('common.save') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- Bulk import modal -->
    <Teleport to="body">
      <div v-if="showBulkImport" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" @click.self="showBulkImport = false">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-2xl shadow-xl max-h-[90vh] overflow-y-auto">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-xl font-bold">📦 {{ $t('bulk.title') }}</h2>
            <button @click="showBulkImport = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none">✕</button>
          </div>
          <BulkImport :property-id="propertyId" @done="showBulkImport = false; fetchMeters()" />
        </div>
      </div>
    </Teleport>

    <!-- Delete confirmation modal -->
    <Teleport to="body">
      <div v-if="deleteTarget" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="deleteTarget = null">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-2">{{ $t('meters.delete_confirm_title') }}</h2>
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">{{ $t('meters.delete_confirm_msg') }}</p>
          <div class="flex gap-2 justify-end">
            <button @click="deleteTarget = null" class="px-4 py-2 rounded-lg border dark:border-gray-600">
              {{ $t('common.cancel') }}
            </button>
            <button :disabled="deleting" @click="doDeleteMeter" class="px-4 py-2 bg-red-600 text-white rounded-lg font-medium">
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
import { RouterLink, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import MeterTypeIcon from '@/components/MeterTypeIcon.vue'
import BulkImport from '@/components/BulkImport.vue'

const authStore = useAuthStore()
const route = useRoute()
const propertyId = route.params.propertyId as string

const loading = ref(true)
const meters = ref<any[]>([])
const showCreate = ref(false)
const showBulkImport = ref(false)
const newMeter = ref({
  name: '', meter_type: 'water', unit: 'm³',
  serial_number: '', location: '',
})

const editTarget = ref<any>(null)
const editForm = ref({ name: '', serial_number: '', location: '' })
const editSaving = ref(false)
const deleteTarget = ref<any>(null)
const deleting = ref(false)

onMounted(fetchMeters)

async function fetchMeters() {
  loading.value = true
  const res = await apiFetch(`/api/properties/${propertyId}/meters`, {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) meters.value = await res.json()
  loading.value = false
}

async function createMeter() {
  const res = await apiFetch(`/api/properties/${propertyId}/meters`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(newMeter.value),
  })
  if (res.ok) {
    showCreate.value = false
    fetchMeters()
  }
}

function startEditMeter(m: any) {
  editTarget.value = m
  editForm.value = { name: m.name, serial_number: m.serial_number ?? '', location: m.location ?? '' }
}

async function saveEditMeter() {
  if (!editTarget.value) return
  editSaving.value = true
  const res = await apiFetch(`/api/properties/${propertyId}/meters/${editTarget.value.id}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(editForm.value),
  })
  editSaving.value = false
  if (res.ok) { editTarget.value = null; await fetchMeters() }
}

function confirmDeleteMeter(m: any) {
  deleteTarget.value = m
}

async function doDeleteMeter() {
  if (!deleteTarget.value) return
  deleting.value = true
  const res = await apiFetch(`/api/properties/${propertyId}/meters/${deleteTarget.value.id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  deleting.value = false
  if (res.ok) { deleteTarget.value = null; await fetchMeters() }
}
</script>
