<template>
  <div class="flex flex-col gap-4">

    <!-- ── Stage: upload ── -->
    <template v-if="stage === 'upload'">
      <div
        class="border-2 border-dashed dark:border-gray-600 rounded-xl p-8 text-center cursor-pointer
               hover:border-brand-400 hover:bg-brand-50 dark:hover:bg-gray-750 transition select-none"
        :class="{ 'border-brand-400 bg-brand-50 dark:bg-gray-750': dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
        @click="fileInputRef?.click()"
      >
        <div class="text-4xl mb-2">📦</div>
        <p class="font-semibold text-gray-700 dark:text-gray-200">{{ $t('bulk.drop_hint') }}</p>
        <p class="text-sm text-gray-400 mt-1">{{ $t('bulk.drop_sub') }}</p>
        <input
          ref="fileInputRef"
          type="file"
          accept="image/*"
          multiple
          class="hidden"
          @change="onFilesSelected"
        />
      </div>

      <div v-if="selectedFiles.length" class="text-sm text-gray-500 dark:text-gray-400 text-center">
        {{ selectedFiles.length }} {{ $t('bulk.files_selected') }}
      </div>

      <!-- Engine selector -->
      <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500 whitespace-nowrap">{{ $t('ocr.engine_mode') }}</label>
        <select v-model="engine" class="flex-1 text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5">
          <option value="auto">{{ $t('ocr.engine_auto') }}</option>
          <option value="ocr">{{ $t('ocr.engine_ocr') }}</option>
          <option value="llm">{{ $t('ocr.engine_llm') }}</option>
        </select>
      </div>

      <button
        :disabled="!selectedFiles.length"
        @click="startScan"
        class="px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 text-white rounded-xl font-semibold transition"
      >
        {{ $t('bulk.start_scan') }}
      </button>
    </template>

    <!-- ── Stage: scanning ── -->
    <template v-else-if="stage === 'scanning'">
      <div class="text-center text-sm text-gray-500 dark:text-gray-400 mb-2">
        {{ $t('bulk.scanning_progress', { done: scanDone, total: selectedFiles.length }) }}
      </div>
      <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2 mb-4">
        <div
          class="bg-brand-600 h-2 rounded-full transition-all"
          :style="{ width: `${scanDone / selectedFiles.length * 100}%` }"
        ></div>
      </div>
      <ul class="flex flex-col gap-1 max-h-64 overflow-y-auto text-sm">
        <li v-for="(f, i) in selectedFiles" :key="i" class="flex items-center gap-2 px-2 py-1">
          <span v-if="i < scanDone" class="text-green-500">✔</span>
          <span v-else-if="i === scanDone" class="animate-spin text-brand-500">⏳</span>
          <span v-else class="text-gray-300 dark:text-gray-600">○</span>
          <span class="truncate text-gray-700 dark:text-gray-300">{{ f.name }}</span>
        </li>
      </ul>
    </template>

    <!-- ── Stage: review ── -->
    <template v-else-if="stage === 'review'">
      <!-- Summary bar -->
      <div class="flex flex-wrap items-center gap-3 text-sm">
        <span class="text-green-600 dark:text-green-400 font-medium">
          ✔ {{ exactCount }} {{ $t('bulk.match_exact') }}
        </span>
        <span class="text-yellow-600 dark:text-yellow-400 font-medium">
          ⚠ {{ partialCount }} {{ $t('bulk.match_partial') }}
        </span>
        <span class="text-red-500 dark:text-red-400 font-medium">
          ✕ {{ noneCount }} {{ $t('bulk.match_none') }}
        </span>
        <div class="ml-auto flex items-center gap-2">
          <label class="text-xs text-gray-500">{{ $t('bulk.all_date') }}</label>
          <input
            type="datetime-local"
            v-model="globalDate"
            @change="applyGlobalDate"
            class="text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1"
          />
        </div>
      </div>

      <!-- Review items -->
      <div class="flex flex-col gap-3 max-h-[60vh] overflow-y-auto pr-1">
        <div
          v-for="(item, i) in reviewItems"
          :key="i"
          class="rounded-xl border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm"
          :class="{
            'border-green-300 dark:border-green-700': item.match_confidence === 'exact',
            'border-yellow-300 dark:border-yellow-700': item.match_confidence === 'partial',
            'border-red-300 dark:border-red-700': item.match_confidence === 'none',
          }"
        >
          <div class="flex gap-3 p-3">
            <!-- Thumbnail -->
            <div class="shrink-0 flex flex-col items-center gap-1">
              <img
                v-if="item.temp_image_path"
                :src="blobUrls[item.temp_image_path] ?? ''"
                class="w-20 h-20 object-cover rounded-lg border dark:border-gray-600 cursor-pointer"
                loading="lazy"
                :title="$t('bulk.crop_rescan')"
                @click="openCropForItem(i)"
              />
              <div v-else class="w-20 h-20 bg-gray-100 dark:bg-gray-700 rounded-lg flex items-center justify-center text-gray-400 text-2xl">
                ❌
              </div>
              <button
                v-if="item.temp_image_path"
                @click="openCropForItem(i)"
                class="text-xs px-2 py-0.5 rounded border border-brand-300 dark:border-brand-700 text-brand-500 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                :title="$t('bulk.crop_rescan')"
              >✂️</button>
            </div>

            <!-- Details -->
            <div class="flex-1 flex flex-col gap-2 min-w-0">
              <!-- Filename + exclude button -->
              <div class="flex items-start justify-between gap-2">
                <span class="text-xs text-gray-500 truncate">{{ item.original_filename }}</span>
                <button
                  @click="reviewItems.splice(i, 1)"
                  class="shrink-0 text-xs px-2 py-0.5 rounded border border-red-200 dark:border-red-800 text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                  :title="$t('bulk.delete_item')"
                >
                  🗑
                </button>
              </div>

              <!-- Error -->
              <div v-if="item.error" class="text-xs text-red-500">{{ item.error }}</div>

              <!-- Serial + match badge -->
              <div v-else class="flex flex-wrap items-center gap-2">
                <span class="text-xs text-gray-400">
                  {{ $t('bulk.serial') }}: <span class="font-mono text-gray-700 dark:text-gray-200">{{ item.detected_serial || '–' }}</span>
                </span>
                <span
                  class="text-xs font-medium px-1.5 py-0.5 rounded-full"
                  :class="{
                    'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300': item.match_confidence === 'exact',
                    'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300': item.match_confidence === 'partial',
                    'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400': item.match_confidence === 'none',
                  }"
                >
                  {{ item.match_confidence === 'exact' ? $t('bulk.exact') : item.match_confidence === 'partial' ? $t('bulk.partial') : $t('bulk.unmatched') }}
                </span>
                <span
                  v-if="item.detection_method"
                  class="text-xs font-medium px-1.5 py-0.5 rounded-full"
                  :class="{
                    'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300': item.detection_method === 'llm',
                    'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300': item.detection_method === 'ocr',
                  }"
                >
                  {{ item.detection_method === 'llm' ? 'LLM' : 'OCR' }}
                </span>
              </div>

              <!-- Meter selector -->
              <select
                v-if="!item.error"
                v-model="item.selected_meter_id"
                class="text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5 w-full"
              >
                <option value="">— {{ $t('bulk.select_meter') }} —</option>
                <optgroup v-if="item.matched_meter || item.candidate_meters.length" :label="$t('bulk.matches')">
                  <option v-if="item.matched_meter" :value="item.matched_meter.id">
                    {{ item.matched_meter.name }}
                    <template v-if="item.matched_meter.serial_number"> ({{ item.matched_meter.serial_number }})</template>
                  </option>
                  <option v-for="c in item.candidate_meters" :key="c.id" :value="c.id">
                    {{ c.name }}
                    <template v-if="c.serial_number"> ({{ c.serial_number }})</template>
                  </option>
                </optgroup>
                <optgroup :label="$t('bulk.all_meters')">
                  <option
                    v-for="m in allMeters"
                    :key="m.id"
                    :value="m.id"
                    :disabled="m.id === item.matched_meter?.id || item.candidate_meters.some(c => c.id === m.id)"
                  >
                    {{ m.name }}
                    <template v-if="m.serial_number"> ({{ m.serial_number }})</template>
                  </option>
                </optgroup>
              </select>

              <!-- Value + date row -->
              <div v-if="!item.error" class="flex flex-col sm:flex-row gap-2">
                <input
                  v-model="item.confirmed_value"
                  type="text"
                  inputmode="decimal"
                  :placeholder="$t('reading.enter_value')"
                  class="w-full sm:flex-1 sm:min-w-0 text-sm rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5 font-mono"
                />
                <input
                  v-model="item.read_at"
                  type="datetime-local"
                  class="w-full sm:w-auto text-sm rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Action bar -->
      <div class="flex items-center gap-3 pt-2 border-t dark:border-gray-700">
        <button
          @click="stage = 'upload'; selectedFiles = []; reviewItems = []"
          class="px-4 py-2 border dark:border-gray-600 rounded-xl text-sm hover:bg-gray-50 dark:hover:bg-gray-700 transition"
        >
          {{ $t('common.cancel') }}
        </button>
        <button
          @click="commitAll"
          :disabled="committing || !commitCount"
          class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 text-white rounded-xl font-semibold transition"
        >
          {{ committing
              ? $t('bulk.saving_progress', { done: commitDone, total: commitCount })
              : $t('bulk.save_all', { n: commitCount }) }}
        </button>
      </div>
    </template>

    <!-- ── Stage: done ── -->
    <template v-else-if="stage === 'done'">
      <div class="text-center py-6 flex flex-col items-center gap-3">
        <div class="text-5xl">🎉</div>
        <p class="text-lg font-semibold">{{ $t('bulk.done_title') }}</p>
        <p class="text-sm text-gray-500">
          {{ $t('bulk.done_summary', { saved: commitSaved, failed: commitFailed }) }}
        </p>
        <div v-if="commitErrors.length" class="text-xs text-red-500 text-left w-full max-w-sm">
          <p v-for="(e, i) in commitErrors" :key="i">{{ e }}</p>
        </div>
        <button
          @click="$emit('done')"
          class="mt-2 px-6 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold transition"
        >
          {{ $t('common.close') }}
        </button>
      </div>
    </template>

  </div>
  <!-- ── Crop modal ──────────────────────────────────────────────────────── -->
  <Teleport to="body">
    <div
      v-if="cropIndex !== null"
      class="fixed inset-0 bg-black/70 flex items-center justify-center z-[100] p-4"
      @click.self="closeCropModal"
    >
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md flex flex-col gap-3 p-4 max-h-[90vh] overflow-y-auto">
        <!-- Display crop -->
        <template v-if="cropStage === 'display'">
          <p class="text-sm font-semibold text-gray-700 dark:text-gray-200">✂️ {{ $t('bulk.crop_display_title') }}</p>
          <p class="text-xs text-gray-500">{{ $t('bulk.crop_display_hint') }}</p>
          <div style="height: 360px; border-radius: 0.75rem; overflow: hidden;">
            <Cropper ref="cropCropperRef" :src="cropOriginalUrl ?? ''" class="h-full" />
          </div>
          <div class="flex gap-2">
            <button @click="cropCropperRef?.reset()" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
              {{ $t('ocr.crop_reset') }}
            </button>
            <button @click="cropGoToSerial" class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold text-sm transition">
              {{ $t('ocr.scan') }}
            </button>
            <button @click="closeCropModal" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
              {{ $t('common.cancel') }}
            </button>
          </div>
        </template>

        <!-- Serial crop -->
        <template v-else-if="cropStage === 'serial'">
          <p class="text-sm font-semibold text-gray-700 dark:text-gray-200">{{ $t('ocr.serial_crop_title') }}</p>
          <p class="text-xs text-gray-500">{{ $t('ocr.serial_crop_hint') }}</p>
          <div style="height: 320px; border-radius: 0.75rem; overflow: hidden;">
            <Cropper ref="cropSerialCropperRef" :src="cropOriginalUrl ?? ''" class="h-full" />
          </div>
          <p v-if="cropError" class="text-xs text-red-500">{{ cropError }}</p>
          <div class="flex gap-2">
            <button @click="cropSerialCropperRef?.reset()" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
              {{ $t('ocr.crop_reset') }}
            </button>
            <button @click="cropSubmit(true)" class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold text-sm transition">
              {{ $t('ocr.serial_scan') }}
            </button>
            <button @click="cropSubmit(false)" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
              {{ $t('ocr.serial_skip') }}
            </button>
          </div>
        </template>

        <!-- Scanning -->
        <template v-else-if="cropStage === 'scanning'">
          <div class="py-10 text-center text-sm text-gray-500 dark:text-gray-400 animate-pulse">{{ $t('ocr.scanning') }}</div>
        </template>
      </div>
    </div>
  </Teleport>

</template>

<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { Cropper } from 'vue-advanced-cropper'
import 'vue-advanced-cropper/dist/style.css'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'

const { t } = useI18n()
const authStore = useAuthStore()

const props = defineProps<{
  propertyId?: string
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

// ── State ──────────────────────────────────────────────────────────────────────

type MatchConf = 'exact' | 'partial' | 'none'

interface MeterSummary {
  id: string
  property_id: string
  name: string
  serial_number: string | null
  meter_type: string
  unit: string
}

interface ScanResult {
  original_filename: string
  temp_image_path: string | null
  exif_date: string | null
  detected_value: string | null
  detected_serial: string | null
  detection_method: 'ocr' | 'llm' | null
  matched_meter: MeterSummary | null
  match_confidence: MatchConf
  candidate_meters: MeterSummary[]
  error: string | null
}

interface ReviewItem extends ScanResult {
  selected_meter_id: string
  confirmed_value: string
  read_at: string
}

const stage = ref<'upload' | 'scanning' | 'review' | 'done'>('upload')
const engine = ref('auto')
const dragging = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFiles = ref<File[]>([])
const scanDone = ref(0)
const reviewItems = ref<ReviewItem[]>([])
const allMeters = ref<MeterSummary[]>([])
const _now = new Date()
const globalDate = ref(`${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, '0')}-${String(_now.getDate()).padStart(2, '0')}T${String(_now.getHours()).padStart(2, '0')}:${String(_now.getMinutes()).padStart(2, '0')}`)

const blobUrls = ref<Record<string, string>>({})

function revokeBlobUrls() {
  for (const url of Object.values(blobUrls.value)) URL.revokeObjectURL(url)
  blobUrls.value = {}
}

async function loadBlobUrl(path: string): Promise<string | null> {
  const filename = path.replace('uploads/', '')
  try {
    const res = await apiFetch(`/api/uploads/${filename}`, {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    if (res.ok) {
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      blobUrls.value[path] = url
      return url
    }
  } catch { /* silently ignore, img will just be missing */ }
  return null
}

onUnmounted(revokeBlobUrls)

// ── Crop modal ────────────────────────────────────────────────────────────────

type CropStage = 'display' | 'serial' | 'scanning'
const cropIndex = ref<number | null>(null)
const cropStage = ref<CropStage>('display')
const cropOriginalUrl = ref<string | null>(null)
const cropDisplayBlob = ref<Blob | null>(null)
const cropCropperRef = ref<InstanceType<typeof Cropper> | null>(null)
const cropSerialCropperRef = ref<InstanceType<typeof Cropper> | null>(null)
const cropError = ref('')
const cropScanning = ref(false)

async function openCropForItem(index: number) {
  const item = reviewItems.value[index]
  if (!item.temp_image_path) return
  let url = blobUrls.value[item.temp_image_path]
  if (!url) url = (await loadBlobUrl(item.temp_image_path)) ?? ''
  if (!url) return
  cropOriginalUrl.value = url
  cropIndex.value = index
  cropStage.value = 'display'
  cropDisplayBlob.value = null
  cropError.value = ''
}

function closeCropModal() {
  cropIndex.value = null
  cropOriginalUrl.value = null
  cropDisplayBlob.value = null
  cropError.value = ''
  cropScanning.value = false
}

async function cropGoToSerial() {
  if (!cropCropperRef.value) return
  const { canvas } = cropCropperRef.value.getResult()
  if (!canvas) return
  const blob: Blob = await new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/jpeg', 0.92),
  )
  cropDisplayBlob.value = blob
  cropStage.value = 'serial'
}

async function cropSubmit(withSerial: boolean) {
  if (!cropDisplayBlob.value || cropIndex.value === null) return
  cropStage.value = 'scanning'
  cropError.value = ''

  const form = new FormData()
  form.append('file', cropDisplayBlob.value, 'display.jpg')
  form.append('engine', engine.value)
  form.append('already_cropped', 'true')

  if (withSerial && cropSerialCropperRef.value) {
    const { canvas: sc } = cropSerialCropperRef.value.getResult()
    if (sc) {
      const serialBlob: Blob = await new Promise((resolve, reject) =>
        sc.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/jpeg', 0.92),
      )
      form.append('serial_file', serialBlob, 'serial.jpg')
    }
  }

  try {
    const res = await apiFetch('/api/ocr/scan', {
      method: 'POST',
      headers: { Authorization: `Bearer ${authStore.token}` },
      body: form,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()

    const item = reviewItems.value[cropIndex.value]
    if (data.detected_value) item.confirmed_value = data.detected_value
    if (data.detected_serial != null) item.detected_serial = data.detected_serial
    if (data.detection_method) item.detection_method = data.detection_method

    // Replace stored image path + reload thumbnail with the new cropped image
    if (data.image_path) {
      item.temp_image_path = data.image_path
      loadBlobUrl(data.image_path)
    }

    // Re-run serial matching against allMeters
    if (data.detected_serial) {
      const norm = (s: string) => s.replace(/[\s\-\.]/g, '').toUpperCase()
      const detected = norm(data.detected_serial)
      const exact = allMeters.value.filter(m => m.serial_number && norm(m.serial_number) === detected)
      const partial = allMeters.value.filter(m => m.serial_number && (
        norm(m.serial_number).includes(detected) || detected.includes(norm(m.serial_number))
      ))
      if (exact.length === 1) {
        item.matched_meter = exact[0]
        item.match_confidence = 'exact'
        item.candidate_meters = []
        item.selected_meter_id = exact[0].id
      } else if (partial.length === 1) {
        item.matched_meter = partial[0]
        item.match_confidence = 'partial'
        item.candidate_meters = []
        item.selected_meter_id = partial[0].id
      }
    }

    closeCropModal()
  } catch (err) {
    cropError.value = String(err)
    cropStage.value = 'serial'
  }
}

const committing = ref(false)
const commitDone = ref(0)
const commitSaved = ref(0)
const commitFailed = ref(0)
const commitErrors = ref<string[]>([])

// ── Computed ──────────────────────────────────────────────────────────────────

const includedItems = computed(() =>
  reviewItems.value.filter(i => !i.error && i.selected_meter_id && i.confirmed_value),
)
const commitCount = computed(() => includedItems.value.length)

const exactCount = computed(() => reviewItems.value.filter(i => i.match_confidence === 'exact').length)
const partialCount = computed(() => reviewItems.value.filter(i => i.match_confidence === 'partial').length)
const noneCount = computed(() => reviewItems.value.filter(i => i.match_confidence === 'none').length)

// ── File selection ─────────────────────────────────────────────────────────────

function onDrop(e: DragEvent) {
  dragging.value = false
  const files = e.dataTransfer?.files
  if (files) selectedFiles.value = Array.from(files)
}

function onFilesSelected(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) selectedFiles.value = Array.from(input.files)
}

// ── Scan ──────────────────────────────────────────────────────────────────────

async function startScan() {
  stage.value = 'scanning'
  scanDone.value = 0
  reviewItems.value = []

  const form = new FormData()
  for (const f of selectedFiles.value) form.append('files', f)
  if (props.propertyId) form.append('property_id', props.propertyId)
  form.append('engine', engine.value)

  const now = new Date()
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`

  try {
    const res = await fetch('/api/ocr/bulk-scan', {
      method: 'POST',
      headers: { Authorization: `Bearer ${authStore.token}` },
      body: form,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    if (!res.body) throw new Error('No response body')

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE messages are separated by double newlines
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''

      for (const part of parts) {
        const lines = part.split('\n')
        let eventName = ''
        let dataLine = ''

        for (const line of lines) {
          if (line.startsWith('event:')) {
            eventName = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLine = line.slice(5).trim()
          }
        }

        if (!dataLine) continue

        const parsed = JSON.parse(dataLine)

        if (eventName === 'complete') {
          // Final event — contains the full meter list
          allMeters.value = parsed.meters ?? []
          stage.value = 'review'
        } else {
          // Per-image result event
          const item = {
            ...(parsed as ScanResult),
            selected_meter_id: (parsed as ScanResult).matched_meter?.id ?? '',
            confirmed_value: (parsed as ScanResult).detected_value ?? '',
            read_at: (parsed as ScanResult).exif_date ?? today,
          }
          reviewItems.value.push(item)
          scanDone.value++
          if (item.temp_image_path) loadBlobUrl(item.temp_image_path)
        }
      }


    }
  } catch (err) {
    console.error('Bulk scan failed', err)
    // Fall back to error display on each item
    reviewItems.value = selectedFiles.value.map(f => ({
      original_filename: f.name,
      temp_image_path: null,
      detected_value: null,
      detected_serial: null,
      matched_meter: null,
      match_confidence: 'none',
      candidate_meters: [],
      error: String(err),
      selected_meter_id: '',
      confirmed_value: '',
      read_at: today,
      exif_date: null,
    }))
  } finally {
    // Ensure we always land in review (e.g. on connection drop mid-stream)
    if (stage.value === 'scanning') stage.value = 'review'
  }
}

// ── Global date helper ────────────────────────────────────────────────────────

function applyGlobalDate() {
  for (const item of reviewItems.value) {
    item.read_at = globalDate.value
  }
}

// ── Commit ────────────────────────────────────────────────────────────────────

async function commitAll() {
  committing.value = true
  commitDone.value = 0
  commitSaved.value = 0
  commitFailed.value = 0
  commitErrors.value = []

  const token = authStore.token
  const items = includedItems.value

  // Resolve meter → property mapping
  const meterMap = new Map<string, MeterSummary>()
  for (const m of allMeters.value) meterMap.set(m.id, m)
  for (const item of reviewItems.value) {
    if (item.matched_meter) meterMap.set(item.matched_meter.id, item.matched_meter)
    for (const c of item.candidate_meters) meterMap.set(c.id, c)
  }

  for (const item of items) {
    const meter = meterMap.get(item.selected_meter_id)
    if (!meter) {
      commitFailed.value++
      commitErrors.value.push(`${item.original_filename}: no meter found`)
      commitDone.value++
      continue
    }

    try {
      const body = {
        value: parseFloat(item.confirmed_value.replace(',', '.')),
        read_at: new Date(item.read_at).toISOString(),
        source: 'manual',
        image_path: item.temp_image_path,
      }

      const res = await fetch(
        `/api/properties/${meter.property_id}/meters/${meter.id}/readings/`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
        },
      )
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      commitSaved.value++
    } catch (err) {
      commitFailed.value++
      commitErrors.value.push(`${item.original_filename}: ${err}`)
    }
    commitDone.value++
  }

  committing.value = false
  stage.value = 'done'
}
</script>
