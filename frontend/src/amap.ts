import type { Coordinates, Destination } from './api'

type LngLat = { getLng(): number; getLat(): number }
type MapEvent = { lnglat: LngLat }
type MapInstance = {
  on(type: string, handler: (event: MapEvent) => void): void
  off(type: string, handler: (event: MapEvent) => void): void
  setZoomAndCenter(zoom: number, center: Coordinates): void
  lngLatToContainer(point: LngLat): { getX(): number; getY(): number }
  destroy(): void
}
type Marker = {
  on(type: string, handler: (event: MapEvent) => void): void
  off(type: string, handler: (event: MapEvent) => void): void
  setPosition(position: Coordinates): void
  setMap(map: MapInstance | null): void
}
type Place = { name: string; address?: string; location?: LngLat }
type sdk = {
  Map: new (element: HTMLElement, options: object) => MapInstance
  LngLat: new (lng: number, lat: number) => LngLat
  Marker: new (options: object) => Marker
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

const key = import.meta.env.VITE_AMAP_JS_KEY?.trim()
const serviceHost = import.meta.env.VITE_AMAP_SERVICE_HOST?.trim()
export const amapConfigured = Boolean(key && serviceHost)
let loader: Promise<sdk> | null = null

function load(): Promise<sdk> {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loader) return loader
  loader = new Promise<sdk>((resolve, reject) => {
    window._AMapSecurityConfig = { serviceHost }
    const script = document.createElement('script')
    const timeout = window.setTimeout(() => fail(), 10000)
    function fail() {
      window.clearTimeout(timeout)
      script.remove()
      reject(new Error('高德底图加载失败，可继续使用离线坐标视图。'))
    }
    script.src = 'https://webapi.amap.com/maps?' + new URLSearchParams({
      v: '2.0', key, plugin: 'AMap.PlaceSearch',
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
    throw error
  })
  return loader
}

export async function createAmap(
  element: HTMLElement,
  center: Destination,
  onSelect: (point: Destination) => void,
  onDraw: () => void,
) {
  const api = await load()
  const map = new api.Map(element, {
    center: [center.lng, center.lat], zoom: 11, viewMode: '2D',
  })
  const select = (event: MapEvent) => onSelect({
    lng: event.lnglat.getLng(), lat: event.lnglat.getLat(),
  })
  const marker = new api.Marker({ position: [center.lng, center.lat], draggable: true })
  marker.setMap(map)
  marker.on('dragend', select)
  map.on('click', select)
  const renderEvents = ['mapmove', 'zoomchange', 'moveend', 'zoomend', 'complete']
  renderEvents.forEach(event => map.on(event, onDraw))

  return {
    fit(point: Destination, radius: number) {
      const zoom = Math.max(8, Math.min(15, Math.round(11 + Math.log2(15000 / radius))))
      marker.setPosition([point.lng, point.lat])
      map.setZoomAndCenter(zoom, [point.lng, point.lat])
    },
    project([lng, lat]: Coordinates): Coordinates {
      const point = map.lngLatToContainer(new api.LngLat(lng, lat))
      return [point.getX(), point.getY()]
    },
    destroy() {
      renderEvents.forEach(event => map.off(event, onDraw))
      map.off('click', select)
      marker.off('dragend', select)
      marker.setMap(null)
      map.destroy()
    },
  }
}

export async function searchAmap(query: string): Promise<{ name: string; point: Destination }[]> {
  const api = await load()
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
