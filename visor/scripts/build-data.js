/**
 * build-data.js
 * Reads union-trees.geojson and produces:
 *   public/data/clusters.geojson      — one centroid Point per parcel (id + count + bbox)
 *   public/data/group-{id}.geojson   — all individual trees per parcel (lazy-loaded)
 *   public/tiles/{z}/{x}/{y}.pbf     — MVT tiles for the full tree dataset (z12-19)
 *   public/tiles/metadata.json
 *
 * Run: node scripts/build-data.js
 */

import { readFileSync, mkdirSync, writeFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'
import geojsonvt from 'geojson-vt'
import vtpbf from 'vt-pbf'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(__dirname, '..')

const MIN_ZOOM = 12
const MAX_ZOOM = 19

// ── Read source ──────────────────────────────────────────────────────────────
console.log('Reading union-trees.geojson…')
const geojson = JSON.parse(readFileSync(resolve(ROOT, 'union-trees.geojson'), 'utf-8'))
console.log(`  ${geojson.features.length} features`)

// ── Group by parcel id ───────────────────────────────────────────────────────
const groups = new Map()
for (const f of geojson.features) {
  const id = f.properties.id
  if (!groups.has(id)) groups.set(id, [])
  groups.get(id).push(f)
}
console.log(`  ${groups.size} unique parcels`)

// ── Compute clusters (one centroid per parcel) ───────────────────────────────
const clusterFeatures = []
for (const [id, features] of groups) {
  const lons = features.map(f => f.geometry.coordinates[0])
  const lats = features.map(f => f.geometry.coordinates[1])
  const cx = lons.reduce((a, b) => a + b, 0) / lons.length
  const cy = lats.reduce((a, b) => a + b, 0) / lats.length

  clusterFeatures.push({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [cx, cy] },
    properties: {
      id,
      count: features.length,
      // bbox as array: [minLon, minLat, maxLon, maxLat]
      bbox: [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)],
    },
  })
}

// ── Write cluster + group files ──────────────────────────────────────────────
const dataDir = resolve(ROOT, 'public', 'data')
mkdirSync(dataDir, { recursive: true })

writeFileSync(
  resolve(dataDir, 'clusters.geojson'),
  JSON.stringify({ type: 'FeatureCollection', features: clusterFeatures })
)
console.log(`✓ clusters.geojson (${clusterFeatures.length} parcels)`)

for (const [id, features] of groups) {
  writeFileSync(
    resolve(dataDir, `group-${id}.geojson`),
    JSON.stringify({ type: 'FeatureCollection', features })
  )
}
console.log(`✓ ${groups.size} group-{id}.geojson files`)

// ── Tile helpers ─────────────────────────────────────────────────────────────
function lon2tile(lon, z) {
  return Math.floor((lon + 180) / 360 * (1 << z))
}
function lat2tile(lat, z) {
  const r = lat * Math.PI / 180
  return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * (1 << z))
}

// ── Bounding box of the whole dataset ────────────────────────────────────────
const allLons = geojson.features.map(f => f.geometry.coordinates[0])
const allLats = geojson.features.map(f => f.geometry.coordinates[1])
const minLon = Math.min(...allLons)
const maxLon = Math.max(...allLons)
const minLat = Math.min(...allLats)
const maxLat = Math.max(...allLats)

// ── Build tile index ─────────────────────────────────────────────────────────
console.log('Building tile index…')
const tileIndex = geojsonvt(geojson, {
  maxZoom: MAX_ZOOM,
  minZoom: MIN_ZOOM,
  tolerance: 3,
  extent: 4096,
  buffer: 64,
  generateId: true,
})

// ── Write pbf tiles ──────────────────────────────────────────────────────────
const tilesDir = resolve(ROOT, 'public', 'tiles')
mkdirSync(tilesDir, { recursive: true })

let tileCount = 0
for (let z = MIN_ZOOM; z <= MAX_ZOOM; z++) {
  const x0 = lon2tile(minLon, z)
  const x1 = lon2tile(maxLon, z)
  const y0 = lat2tile(maxLat, z)   // maxLat → smaller y (north)
  const y1 = lat2tile(minLat, z)   // minLat → larger y  (south)

  for (let x = x0; x <= x1; x++) {
    for (let y = y0; y <= y1; y++) {
      const tile = tileIndex.getTile(z, x, y)
      if (!tile || !tile.features.length) continue

      const dir = resolve(tilesDir, String(z), String(x))
      mkdirSync(dir, { recursive: true })

      const pbf = vtpbf.fromGeojsonVt({ trees: tile })
      writeFileSync(resolve(dir, `${y}.pbf`), pbf)
      tileCount++
    }
  }
}
console.log(`✓ ${tileCount} tiles (z${MIN_ZOOM}–${MAX_ZOOM})`)

// ── Write metadata ───────────────────────────────────────────────────────────
writeFileSync(
  resolve(tilesDir, 'metadata.json'),
  JSON.stringify({
    name: 'arboles-fontanars',
    format: 'pbf',
    minzoom: MIN_ZOOM,
    maxzoom: MAX_ZOOM,
    bounds: [minLon, minLat, maxLon, maxLat],
    center: [(minLon + maxLon) / 2, (minLat + maxLat) / 2, 15],
  })
)
console.log('✓ metadata.json')
console.log('Done.')
