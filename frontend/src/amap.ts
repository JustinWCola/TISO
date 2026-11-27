import type { Coordinates, Destination, Result, Sample } from './api'

type LngLat = { getLng(): number; getLat(): number }
type MapEvent = { lnglat: LngLat }
type MapInstance = {
  on(type: string, handler: (event: MapEvent) => void): void
  off(type: string, handler: (event: MapEvent) => void): void
  setZoomAndCenter(zoom: number, center: Coordinates): void
  getZoom(): number
  setZoom(zoom: number): void
  addLayer(layer: MapLayer): void
  removeLayer(layer: MapLayer): void
  lngLatToContainer(point: Coordinates): { getX(): number; getY(): number }
  destroy(): void
}
type MapLayer = { render?: () => void }
type Marker = {
  on(type: string, handler: (event: MapEvent) => void): void
  off(type: string, handler: (event: MapEvent) => void): void
  setPosition(position: Coordinates): void
  setMap(map: MapInstance | null): void
}
type Place = { name: string; address?: string; location?: LngLat }
type ReverseGeocode = { formattedAddress?: string; pois?: { name: string; distance?: number | string }[] }
type sdk = {
  Map: new (element: HTMLElement, options: object) => MapInstance
  Marker: new (options: object) => Marker
  CustomLayer?: new (canvas: HTMLCanvasElement, options: object) => MapLayer
  PlaceSearch: new (options: object) => {
    search(query: string, callback: (status: string, result: { poiList?: { pois: Place[] } }) => void): void
  }
  Geocoder: new (options: { radius: number; extensions: 'all' }) => {
    getAddress(point: Coordinates, callback: (status: string, result: { regeocode?: ReverseGeocode }) => void): void
  }
}

declare global {
  interface Window {
    AMap?: sdk
    _AMapSecurityConfig?: { serviceHost: string }
  }
}

export type AmapConfig = { key: string; serviceHost: string }
const key = import.meta.env.VITE_AMAP_JS_KEY?.trim()
const serviceHost = import.meta.env.VITE_AMAP_SERVICE_HOST?.trim()
export const legacyAmapConfig: AmapConfig | null = key && serviceHost ? { key, serviceHost } : null
let loader: Promise<sdk> | null = null
let loadedConfig: AmapConfig | null = null

export function bandColor(index: number, count: number): string {
  const palette = [[79, 179, 152], [168, 206, 124], [235, 207, 113], [233, 166, 91]]
  const position = index * (palette.length - 1) / Math.max(1, count - 1)
  const left = Math.floor(position)
  const right = Math.min(left + 1, palette.length - 1)
  return '#' + palette[left].map((value, channel) =>
    Math.round(value + (palette[right][channel] - value) * (position - left)).toString(16).padStart(2, '0'),
  ).join('')
}

function sampleColor(status: string): string {
  return status === 'ok' ? '#24474a' : '#ad4d24'
}

export function amapNeedsReload(config: AmapConfig | null): boolean {
  return !!loadedConfig && (loadedConfig.key !== config?.key || loadedConfig.serviceHost !== config?.serviceHost)
}

function load(config: AmapConfig): Promise<sdk> {
  if (amapNeedsReload(config)) return Promise.reject(new Error('请刷新页面以应用新的高德底图设置。'))
  if (loader) return loader
  loadedConfig = config
  loader = new Promise<sdk>((resolve, reject) => {
    window._AMapSecurityConfig = { serviceHost: config.serviceHost }
    const script = document.createElement('script')
    const timeout = window.setTimeout(() => fail(), 10000)
    function fail() {
      window.clearTimeout(timeout)
      script.remove()
      reject(new Error('高德底图加载失败'))
    }
    script.src = 'https://webapi.amap.com/maps?' + new URLSearchParams({
      v: '2.0', key: config.key, plugin: 'AMap.PlaceSearch,AMap.Geocoder',
    })
    script.onload = () => {
      window.clearTimeout(timeout)
      if (window.AMap) resolve(window.AMap)
      else fail()
    }
    script.onerror = fail
    document.head.append(script)
  }).catch(error => {
    loader = null
    loadedConfig = null
    throw error
  })
  return loader
}

export async function createAmap(
  element: HTMLElement,
  config: AmapConfig,
  center: Destination,
  radius: number,
  onSelect: (point: Destination) => void,
) {
  const api = await load(config)
  const location: Coordinates = [center.lng, center.lat]
  const map = new api.Map(element, {
    center: location, zoom: zoomForRadius(radius), viewMode: '2D', mapStyle: 'amap://styles/normal',
  })
  const select = (event: MapEvent) => onSelect({
    lng: event.lnglat.getLng(), lat: event.lnglat.getLat(),
  })
  const marker = new api.Marker({ position: location, draggable: true, zIndex: 130 })
  marker.setMap(map)
  marker.on('dragend', select)
  map.on('click', select)
  const canvas = document.createElement('canvas')
  canvas.className = 'amap-travel-canvas'
  const context = canvas.getContext('2d')
  let result: Result | null = null
  let liveSamples: Sample[] | undefined
  let visible: TravelLayers = { bands: true, lines: true, samples: false }
  let metro: ReturnType<typeof prepareMetroLayers> | null = null
  let lastMetroFrame: number[] | null = null

  function project(point: Coordinates): Coordinates {
    const pixel = map.lngLatToContainer(point)
    return [pixel.getX(), pixel.getY()]
  }

  function paint() {
    if (!context) return
    const width = element.clientWidth
    const height = element.clientHeight
    if (!width || !height) return
    const ratio = window.devicePixelRatio || 1
    const pixelWidth = Math.round(width * ratio)
    const pixelHeight = Math.round(height * ratio)
    const transform = metro?.transform(project)
    if (transform) {
      const currentFrame = [width, height, ratio, ...transform]
      if (currentFrame.every((value, index) => value === lastMetroFrame?.[index])) return
      lastMetroFrame = currentFrame
    } else {
      lastMetroFrame = null
    }
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth
      canvas.height = pixelHeight
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, width, height)
    if (metro && transform) {
      metro.paint(context, visible, transform)
      if (visible.samples) paintSamples(context, project, liveSamples ?? result?.samples ?? [])
    } else {
      paintTravelLayers(context, project, result, visible, liveSamples)
    }
  }

  const moveEvents = ['mapmove', 'zoomchange', 'moveend', 'zoomend', 'resize', 'complete']
  let fallback = false
  let frame = 0
  let layer: MapLayer | null = null
  function schedulePaint() {
    if (frame) return
    frame = window.requestAnimationFrame(() => {
      frame = 0
      paint()
    })
  }

  function attachFallback() {
    if (fallback || canvas.isConnected) return
    if (layer) {
      map.removeLayer(layer)
      layer = null
    }
    fallback = true
    canvas.classList.add('amap-travel-fallback')
    element.append(canvas)
    moveEvents.forEach(event => map.on(event, schedulePaint))
    paint()
  }

  if (api.CustomLayer) {
    try {
      layer = new api.CustomLayer(canvas, { zIndex: 120, alwaysRender: true })
      layer.render = paint
      map.addLayer(layer)
    } catch {
      layer = null
      attachFallback()
    }
  } else {
    attachFallback()
  }

  // Some raster renderer configurations accept the layer but never attach its canvas.
  const layerTimeout = window.setTimeout(attachFallback, 1000)

  return {
    zoomBy(step: number) { map.setZoom(Math.max(4, Math.min(18, map.getZoom() + step))) },
    fit(point: Destination, radius: number) {
      const location: Coordinates = [point.lng, point.lat]
      marker.setPosition(location)
      map.setZoomAndCenter(zoomForRadius(radius), location)
      paint()
    },
    setResult(next: Result | null, layers: TravelLayers, points?: Sample[]) {
      if (next !== result) {
        metro = next?.mode === 'metro' && typeof Path2D !== 'undefined' ? prepareMetroLayers(next) : null
      }
      result = next
      visible = layers
      liveSamples = points
      lastMetroFrame = null
      paint()
    },
    destroy() {
      window.clearTimeout(layerTimeout)
      window.cancelAnimationFrame(frame)
      if (layer) map.removeLayer(layer)
      if (fallback) moveEvents.forEach(event => map.off(event, schedulePaint))
      canvas.remove()
      map.off('click', select)
      marker.off('dragend', select)
      marker.setMap(null)
      map.destroy()
    },
  }
}

type TravelLayers = { bands: boolean; lines: boolean; samples: boolean }
type Project = (point: Coordinates) => Coordinates
type MapTransform = [number, number, number, number, number, number]

function trace(target: Pick<Path2D, 'moveTo' | 'lineTo' | 'closePath'>,
  path: Coordinates[], project: Project, close: boolean) {
  path.forEach((point, index) => {
    const [x, y] = project(point)
    if (index === 0) target.moveTo(x, y)
    else target.lineTo(x, y)
  })
  if (close) target.closePath()
}

function mercatorY(latitude: number) {
  return -Math.asinh(Math.tan(latitude * Math.PI / 180)) * 180 / Math.PI
}

function prepareMetroLayers(result: Result) {
  const { lng, lat } = result.destination
  const originY = mercatorY(lat)
  const northDelta = mercatorY(lat + 1) - originY
  const world: Project = ([x, y]) => [x - lng, mercatorY(y) - originY]
  const bands = result.bands.features.map((feature, index, features) => {
    const path = new Path2D()
    for (const polygon of feature.geometry.coordinates) {
      for (const ring of polygon) trace(path, ring, world, true)
    }
    return { path, color: bandColor(index, features.length) }
  })
  const outline = new Path2D()
  for (const feature of result.contours.features) {
    for (const line of feature.geometry.coordinates) trace(outline, line, world, false)
  }

  return {
    transform(project: Project): MapTransform | undefined {
      // The 2D map projects Web Mercator onto the canvas with a single affine transform.
      const [x, y] = project([lng, lat])
      const [eastX, eastY] = project([lng + 1, lat])
      const [northX, northY] = project([lng, lat + 1])
      const a = eastX - x, b = eastY - y
      const c = (northX - x) / northDelta, d = (northY - y) / northDelta
      const matrix: MapTransform = [a, b, c, d, x, y]
      if (matrix.every(Number.isFinite) && Math.abs(a * d - b * c) > 1e-8) return matrix
    },
    paint(context: CanvasRenderingContext2D, visible: TravelLayers, matrix: MapTransform) {
      context.save()
      context.transform(...matrix)
      if (visible.bands) {
        context.globalAlpha = 0.68
        for (const { path, color } of bands) {
          context.fillStyle = color
          context.fill(path, 'evenodd')
        }
        context.globalAlpha = 1
      }
      if (visible.lines) {
        context.strokeStyle = '#324b51'
        context.lineWidth = 1.4 / Math.hypot(matrix[0], matrix[1])
        context.stroke(outline)
      }
      context.restore()
    },
  }
}

function paintSamples(context: CanvasRenderingContext2D, project: Project, samples: Sample[]) {
  for (const sample of samples) {
    if (sample.status === 'outside_scope') continue
    const [x, y] = project(sample.coordinates)
    context.beginPath()
    context.arc(x, y, 2.2, 0, 2 * Math.PI)
    context.fillStyle = sampleColor(sample.status)
    context.fill()
  }
}

export function paintTravelLayers(
  context: CanvasRenderingContext2D,
  project: Project,
  result: Result | null,
  visible: TravelLayers,
  liveSamples?: Sample[],
) {
  if (result && visible.bands) {
    context.globalAlpha = 0.68
    result.bands.features.forEach((feature, index, features) => {
      context.fillStyle = bandColor(index, features.length)
      for (const polygon of feature.geometry.coordinates) {
        context.beginPath()
        for (const ring of polygon) trace(context, ring, project, true)
        context.fill('evenodd')
      }
    })
    context.globalAlpha = 1
  }

  if (result && visible.lines) {
    context.strokeStyle = '#324b51'
    context.lineWidth = 1.4
    for (const feature of result.contours.features) {
      context.beginPath()
      for (const line of feature.geometry.coordinates) trace(context, line, project, false)
      context.stroke()
    }
  }
  if (visible.samples) paintSamples(context, project, liveSamples ?? result?.samples ?? [])
}

function zoomForRadius(radius: number) {
  return Math.max(8, Math.min(15, Math.round(11 + Math.log2(15000 / radius))))
}

export async function searchAmap(config: AmapConfig, query: string): Promise<{ name: string; point: Destination }[]> {
  const api = await load(config)
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error('高德地点搜索超时。')), 10000)
    const search = new api.PlaceSearch({ pageSize: 5, pageIndex: 1 })
    search.search(query, (status, result) => {
      window.clearTimeout(timeout)
      if (status === 'no_data') resolve([])
      else if (status !== 'complete') reject(new Error('高德地点搜索失败。'))
      else resolve((result?.poiList?.pois ?? []).flatMap(place => place.location ? [{
        name: [place.name, place.address].filter(Boolean).join(' · '),
        point: { lng: place.location.getLng(), lat: place.location.getLat() },
      }] : []))
    })
  })
}

export async function identifyAmapPoint(config: AmapConfig, point: Destination): Promise<string> {
  const api = await load(config)
  const radius = 100
  const geocoder = new api.Geocoder({ radius, extensions: 'all' })
  return new Promise(resolve => {
    const timeout = window.setTimeout(() => resolve(''), 3000)
    geocoder.getAddress([point.lng, point.lat], (status, result) => {
      window.clearTimeout(timeout)
      const address = status === 'complete' ? result?.regeocode : undefined
      let nearest = ''
      let nearestDistance = Infinity
      for (const poi of address?.pois ?? []) {
        const distance = parseFloat(String(poi.distance))
        if (poi.name?.trim() && distance >= 0 && distance <= radius && distance < nearestDistance) {
          nearest = poi.name.trim()
          nearestDistance = distance
        }
      }
      resolve(nearest || address?.formattedAddress?.trim() || '')
    })
  })
}
