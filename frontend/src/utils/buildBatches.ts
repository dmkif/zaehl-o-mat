/** Maximum total byte size of files in a single batch.
 *  Stays well below the nginx client_max_body_size (15 MB). */
export const BATCH_MAX_BYTES = 10 * 1024 * 1024 // 10 MB

/** Maximum number of files per batch (safety cap). */
export const BATCH_MAX_FILES = 5

/**
 * Split `files` into batches where each batch does not exceed
 * `maxBytes` total size or `maxFiles` file count.
 * A single file that is larger than `maxBytes` will still be placed
 * in its own batch (so no file is ever dropped).
 */
export function buildBatches(
  files: File[],
  maxBytes: number = BATCH_MAX_BYTES,
  maxFiles: number = BATCH_MAX_FILES,
): File[][] {
  if (files.length === 0) return []

  const batches: File[][] = []
  let current: File[] = []
  let currentSize = 0

  for (const f of files) {
    // Flush the current batch if this file would exceed either limit
    if (current.length > 0 && (currentSize + f.size > maxBytes || current.length >= maxFiles)) {
      batches.push(current)
      current = []
      currentSize = 0
    }
    current.push(f)
    currentSize += f.size
  }

  if (current.length > 0) batches.push(current)
  return batches
}
