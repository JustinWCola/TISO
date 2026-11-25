import type { Coordinates, Destination, Result } from './api'

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
type sdk = {
  Map: new (element: HTMLElement, options: object) => MapInstance
  Marker: new (options: object) => Marker
  CustomLayer?: new (canvas: HTMLCanvasElement, options: object) => MapLayer
  PlaceSearch: new (options: object) => {
    search(query: string, callback: (status: string, result: { poiList?: { pois: Place[] } }) => void): void
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
      v: '2.0', key: config.key, plugin: 'AMap.PlaceSearch',
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
  let boundary = radiusPath(center, radius)
  let result: Result | null = null
  let visible: TravelLayers = { bands: true, lines: true, samples: false }

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
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth
      canvas.height = pixelHeight
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, width, height)
    paintTravelLayers(context, project, boundary, result, visible)
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
      boundary = radiusPath(point, radius)
      map.setZoomAndCenter(zoomForRadius(radius), location)
      paint()
    },
    setResult(next: Result | null, layers: TravelLayers) {
      result = next
      visible = layers
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

function radiusPath(center: Destination, radius: number): Coordinates[] {
  const metersPerDegree = (Math.PI * 6_371_008.8) / 180
  const longitudeScale = metersPerDegree * Math.cos(center.lat * Math.PI / 180)
  return Array.from({ length: 64 }, (_, index) => {
    const angle = index * 2 * Math.PI / 64
    return [
      center.lng + radius * Math.cos(angle) / longitudeScale,
      center.lat + radius * Math.sin(angle) / metersPerDegree,
    ]
  })
}

function paintTravelLayers(
  context: CanvasRenderingContext2D,
  project: (point: Coordinates) => Coordinates,
  boundary: Coordinates[],
  result: Result | null,
  visible: TravelLayers,
) {
  function trace(path: Coordinates[], close: boolean) {
    path.forEach((point, index) => {
      const [x, y] = project(point)
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    })
    if (close) context.closePath()
  }

  if (result && visible.bands) {
    context.globalAlpha = 0.68
    result.bands.features.forEach((feature, index, features) => {
      context.fillStyle = bandColor(index, features.length)
      for (const polygon of feature.geometry.coordinates) {
        context.beginPath()
        for (const ring of polygon) trace(ring, true)
        context.fill('evenodd')
      }
    })
    context.globalAlpha = 1
  }

  context.beginPath()
  trace(boundary, true)
  context.strokeStyle = '#596673'
  context.lineWidth = 1
  context.setLineDash([3, 6])
  context.stroke()
  context.setLineDash([])
  if (!result) return

  if (visible.lines) {
    context.strokeStyle = '#324b51'
    context.lineWidth = 1.4
    for (const feature of result.contours.features) {
      context.beginPath()
      for (const line of feature.geometry.coordinates) trace(line, false)
      context.stroke()
    }
  }
  if (visible.samples) for (const sample of result.samples) {
    const [x, y] = project(sample.coordinates)
    context.beginPath()
    context.arc(x, y, 2.2, 0, 2 * Math.PI)
    context.fillStyle = sampleColor(sample.status)
    context.fill()
  }
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
