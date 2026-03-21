import 'maplibre-gl/dist/maplibre-gl.css'
import maplibregl from 'maplibre-gl'
import './style.css'

// ── Constants ──────────────────────────────────────────────────────────────
const FONTANARS = [-0.7866623, 38.7814255]  // [lng, lat]
const INITIAL_ZOOM = 14

// IGN PNOA-MA — Spain national orthophoto, free, covers full Valencia/Fontanars area
const ICV_TILES = [
  'https://www.ign.es/wmts/pnoa-ma?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0' +
  '&LAYER=OI.OrthoimageCoverage&STYLE=default&FORMAT=image/jpeg' +
  '&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}',
]

// Circle sizes and colours by tree count (step breakpoints)
const COUNT_BREAKS = [50, 200, 600]
const CIRCLE_RADII = [10, 16, 22, 30]
const CIRCLE_COLORS = ['#4CAF50', '#FFA726', '#EF5350', '#B71C1C']

// ── Map ────────────────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      icv: {
        type: 'raster',
        tiles: ICV_TILES,
        tileSize: 256,
        attribution: '© <a href="https://www.ign.es" target="_blank">IGN — PNOA-MA</a>',
      },
    },
    layers: [
      { id: 'icv', type: 'raster', source: 'icv' },
    ],
  },
  center: FONTANARS,
  zoom: INITIAL_ZOOM,
  maxZoom: 20,
})

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

// ── UI elements ────────────────────────────────────────────────────────────
const statsEl = document.createElement('div')
statsEl.id = 'stats'
statsEl.textContent = 'Cargando…'
document.body.appendChild(statsEl)

const btnCenter = document.createElement('button')
btnCenter.id = 'btn-center'
btnCenter.className = 'ctrl-btn'
btnCenter.title = 'Centrar mapa'
btnCenter.innerHTML = '⌖'
btnCenter.addEventListener('click', () => map.flyTo({ center: FONTANARS, zoom: INITIAL_ZOOM }))
document.body.appendChild(btnCenter)

const btnClear = document.createElement('button')
btnClear.id = 'btn-clear'
btnClear.className = 'ctrl-btn'
btnClear.title = 'Cerrar parcela'
btnClear.innerHTML = '✕'
btnClear.addEventListener('click', clearGroupLayer)
document.body.appendChild(btnClear)

buildLegend()

// ── Step expressions helpers ───────────────────────────────────────────────
/** Builds a MapLibre `step` expression for the `count` property */
function stepExpr(prop, values) {
  // ['step', expr, val0, break1, val1, break2, val2, ...]
  const expr = ['step', ['get', prop], values[0]]
  for (let i = 0; i < COUNT_BREAKS.length; i++) {
    expr.push(COUNT_BREAKS[i], values[i + 1])
  }
  return expr
}

// ── Sources & layers ───────────────────────────────────────────────────────
map.on('load', async () => {
  // 1. Cluster source (one point per parcel id)
  const clustersRes = await fetch('./data/clusters.geojson')
  const clustersGJ = await clustersRes.json()

  const total = clustersGJ.features.reduce((s, f) => s + f.properties.count, 0)
  statsEl.textContent = `${total.toLocaleString('es-ES')} árboles detectados`

  map.addSource('clusters', { type: 'geojson', data: clustersGJ })

  // Cluster circles
  map.addLayer({
    id: 'clusters-circle',
    type: 'circle',
    source: 'clusters',
    maxzoom: 18,
    paint: {
      'circle-radius': stepExpr('count', CIRCLE_RADII),
      'circle-color':  stepExpr('count', CIRCLE_COLORS),
      'circle-opacity': 0.88,
      'circle-stroke-width': 2,
      'circle-stroke-color': 'rgba(255,255,255,0.75)',
    },
  })

  // Cluster count labels
  map.addLayer({
    id: 'clusters-label',
    type: 'symbol',
    source: 'clusters',
    maxzoom: 18,
    layout: {
      'text-field': ['to-string', ['get', 'count']],
      'text-font': ['Noto Sans Regular'],
      'text-size': stepExpr('count', [11, 13, 15, 17]),
      'text-allow-overlap': true,
    },
    paint: {
      'text-color': '#fff',
      'text-halo-color': 'rgba(0,0,0,0.3)',
      'text-halo-width': 1,
    },
  })

  // 2. Individual tree dots — vector tiles (visible only at zoom ≥ 18 ≈ 1:1000)
  map.addSource('trees-tiles', {
    type: 'vector',
    tiles: [`${location.origin}${import.meta.env.BASE_URL}tiles/{z}/{x}/{y}.pbf`],
    minzoom: 12,
    maxzoom: 19,
  })

  map.addLayer({
    id: 'trees-dots',
    type: 'circle',
    source: 'trees-tiles',
    'source-layer': 'trees',
    minzoom: 18,
    paint: {
      'circle-radius': 4,
      'circle-color': '#4CAF50',
      'circle-stroke-width': 1,
      'circle-stroke-color': '#fff',
      'circle-opacity': 0.9,
    },
  })

  // 3. Cluster click → load individual parcel trees
  map.on('click', 'clusters-circle', onClusterClick)
  map.on('mouseenter', 'clusters-circle', () => { map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', 'clusters-circle', () => { map.getCanvas().style.cursor = '' })

  // Hide group layer when zooming out below threshold
  map.on('zoom', () => {
    if (map.getZoom() < 16 && map.getSource('group')) {
      clearGroupLayer()
    }
  })
})

// ── Parcel group interaction ───────────────────────────────────────────────
async function onClusterClick(e) {
  const feature = e.features[0]
  const { id, bbox } = feature.properties
  const parsedBbox = typeof bbox === 'string' ? JSON.parse(bbox) : bbox

  // Load individual trees for this parcel
  const res = await fetch(`./data/group-${id}.geojson`)
  if (!res.ok) return
  const gj = await res.json()

  clearGroupLayer()

  map.addSource('group', { type: 'geojson', data: gj })
  map.addLayer({
    id: 'group-dots',
    type: 'circle',
    source: 'group',
    paint: {
      'circle-radius': 5,
      'circle-color': '#FFD600',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#fff',
      'circle-opacity': 0.95,
    },
  })

  btnClear.style.display = 'flex'

  map.fitBounds(
    [[parsedBbox[0], parsedBbox[1]], [parsedBbox[2], parsedBbox[3]]],
    { padding: 60, maxZoom: 19 }
  )
}

function clearGroupLayer() {
  if (map.getLayer('group-dots')) map.removeLayer('group-dots')
  if (map.getSource('group'))     map.removeSource('group')
  btnClear.style.display = 'none'
}

// ── Legend ─────────────────────────────────────────────────────────────────
function buildLegend() {
  const legend = document.createElement('div')
  legend.id = 'legend'

  const title = document.createElement('h3')
  title.textContent = '🌿 Árboles por parcela'
  legend.appendChild(title)

  const ranges = [
    { label: '1 – 50',     color: CIRCLE_COLORS[0], size: CIRCLE_RADII[0] },
    { label: '51 – 200',   color: CIRCLE_COLORS[1], size: CIRCLE_RADII[1] },
    { label: '201 – 600',  color: CIRCLE_COLORS[2], size: CIRCLE_RADII[2] },
    { label: '601+',       color: CIRCLE_COLORS[3], size: CIRCLE_RADII[3] },
  ]

  for (const r of ranges) {
    const row = document.createElement('div')
    row.className = 'legend-row'

    const dot = document.createElement('span')
    dot.className = 'legend-dot'
    dot.style.cssText = `width:${r.size * 2}px;height:${r.size * 2}px;background:${r.color}`

    const label = document.createElement('span')
    label.textContent = r.label

    row.appendChild(dot)
    row.appendChild(label)
    legend.appendChild(row)
  }

  document.body.appendChild(legend)
}
