export type Coordinates = [number, number]
export type Destination = { lng: number; lat: number }
export type Parameters = {
  destination: Destination
  min_spacing_m: number
  max_duration_min: number
  contour_interval_min: number
}
export type Estimate = {
  total_points: number; batches: number; estimated_requests: number; fits_budget: boolean
  point_limit: number; request_limit: number
}
export type Job = {
  job_id: string; status: string; stage: string; sequence: number; total_points: number
  completed_points: number; requests_sent: number; error: { message: string } | null
  preview_ready: boolean
}
export type FeatureCollection<T> = {
  features: { properties: Record<string, number>; geometry: { coordinates: T } }[]
}
export type Sample = { coordinates: Coordinates; duration_s: number | null; status: string }
export type Result = {
  job_id: string; destination: Destination; radius_m: number; spacing_m: number
  preview: boolean; boundary_truncated: boolean
  contours: FeatureCollection<Coordinates[][]>; bands: FeatureCollection<Coordinates[][][]>
  samples: Sample[]
  coverage: number; warnings: string[]; observed_from: string | null; observed_to: string | null
  sample_count: number; success_count: number; request_count: number
}

export const terminal = new Set(['completed', 'partial', 'failed', 'cancelled'])
export const jobs = '/isochrones/jobs'

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const options: RequestInit = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }
  const response = await fetch('/api/v1' + path, { ...options, signal })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error?.message ?? '请求失败')
  return data as T
}

export function destinationFromInputs(lng: string, lat: string): Destination | null {
  if (!lng.trim() || !lat.trim()) return null
  const longitude = Number(lng)
  const latitude = Number(lat)
  if (!(longitude >= 73 && longitude <= 135 && latitude >= 18 && latitude <= 54)) return null
  return { lng: longitude, lat: latitude }
}
