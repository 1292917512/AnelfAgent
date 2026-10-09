// @ts-check
/** Deterministic build-site selection and bounded terrain preparation. */
import { z } from 'zod'
import { Vec3 } from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
const { goals } = pathfinderPkg
import { ToolError } from '../util/errors.js'
import { withTimeout } from '../util/async.js'
import { DEFAULT_ACTION_TIMEOUT_MS } from '../config.js'

/** @typedef {import('../context.js').Context} Context */
/** @typedef {import('./registry.js').Registrar} Registrar */
/** @typedef {{x:number,y:number,z:number}} Coord */

const coord = z.object({ x: z.number().int(), y: z.number().int(), z: z.number().int() })
const PREFERRED = new Set(['grass_block', 'dirt', 'coarse_dirt', 'podzol', 'mycelium'])
const ACCEPTABLE = new Set([...PREFERRED, 'stone', 'cobblestone', 'deepslate'])
const LIQUID = new Set(['water', 'lava'])
const STRUCTURAL_PLANKS = ['oak_planks', 'birch_planks', 'spruce_planks', 'jungle_planks',
  'acacia_planks', 'dark_oak_planks', 'cherry_planks', 'mangrove_planks']
const REPLACEABLE_TEMPLATE_BLOCKS = new Set(['air', 'dirt', 'grass_block', 'coarse_dirt', 'podzol', 'mycelium'])
const FOOTPRINT = 7
const SCAN_UP = 8
const SCAN_DOWN = 24

function air (block) {
  return !block || block.name === 'air' || block.boundingBox === 'empty'
}

function solid (block) {
  return Boolean(block) && block.boundingBox === 'block' && !LIQUID.has(block.name)
}

function safeTerrain (block) {
  return solid(block) && ACCEPTABLE.has(block.name)
}

/** @param {import('mineflayer').Bot} bot @param {number} x @param {number} z @param {number} centerY */
function surfaceColumn (bot, x, z, centerY) {
  for (let y = centerY + SCAN_UP; y >= centerY - SCAN_DOWN; y--) {
    const support = bot.blockAt(new Vec3(x, y, z))
    const feet = bot.blockAt(new Vec3(x, y + 1, z))
    const head = bot.blockAt(new Vec3(x, y + 2, z))
    if (!support || !feet || !head) return null
    if (safeTerrain(support) && air(feet) && air(head)) {
      return { groundY: y, supportName: support.name, preferred: PREFERRED.has(support.name) }
    }
  }
  return null
}

/** @param {import('mineflayer').Bot} bot @param {Coord} home @param {number} footprint @param {number} maxFillDepth */
function inspectSite (bot, home, footprint, maxFillDepth) {
  const columns = []
  let digBlocks = 0
  let fillBlocks = 0
  let preferred = 0
  for (let x = home.x; x < home.x + footprint; x++) {
    for (let z = home.z; z < home.z + footprint; z++) {
      const column = surfaceColumn(bot, x, z, home.y)
      if (!column) return { ok: false, reason: 'UNKNOWN_OR_UNSAFE_COLUMN', columns: [] }
      const delta = column.groundY - (home.y - 1)
      if (Math.abs(delta) > maxFillDepth) {
        return { ok: false, reason: 'TERRAIN_DELTA_TOO_LARGE', columns: [] }
      }
      if (delta > 0) digBlocks += delta
      if (delta < 0) fillBlocks -= delta
      if (column.preferred) preferred++
      columns.push({ x, z, ...column, delta })
    }
  }
  const total = footprint * footprint
  if (preferred < Math.ceil(total * 0.6)) {
    return { ok: false, reason: 'NOT_PLAINS_LIKE', columns: [] }
  }
  return { ok: true, columns, digBlocks, fillBlocks, preferred, total }
}

/** @param {import('mineflayer').Bot} bot @param {Coord} center @param {number} radius @param {number} footprint @param {number} maxFillDepth */
export function findBuildSite (bot, center, radius = 24, footprint = FOOTPRINT, maxFillDepth = 2) {
  const minX = Math.floor(center.x) - radius
  const maxX = Math.floor(center.x) + radius
  const minZ = Math.floor(center.z) - radius
  const maxZ = Math.floor(center.z) + radius
  /** @type {Map<string, ReturnType<typeof surfaceColumn>>} */
  const surfaces = new Map()
  const centerY = Math.floor(center.y)
  for (let x = minX; x <= maxX + footprint; x++) {
    for (let z = minZ; z <= maxZ + footprint; z++) {
      surfaces.set(`${x},${z}`, surfaceColumn(bot, x, z, centerY))
    }
  }
  /** @type {{home: Coord, report: ReturnType<typeof inspectSite>, score: number}[]} */
  const candidates = []
  for (let x = minX; x <= maxX; x++) {
    for (let z = minZ; z <= maxZ; z++) {
      const first = surfaces.get(`${x},${z}`)
      if (!first) continue
      const targetY = first.groundY + 1
      const home = { x, y: targetY, z }
      const report = inspectSite({ blockAt: (p) => surfaces.get(`${p.x},${p.z}`) &&
        (p.y === surfaces.get(`${p.x},${p.z}`).groundY ? { name: surfaces.get(`${p.x},${p.z}`).supportName, boundingBox: 'block' } :
          { name: 'air', boundingBox: 'empty' }) }, home, footprint, maxFillDepth)
      if (!report.ok) continue
      const distance = Math.hypot(x - center.x, z - center.z)
      const score = distance + (totalDelta(report.columns, home.y) * 30) + ((report.total - report.preferred) * 2)
      candidates.push({ home, report, score })
    }
  }
  candidates.sort((a, b) => a.score - b.score)
  const best = candidates.find(candidate => reachableBuildSite(bot, candidate.home))
  if (!best) return { ok: false, reason: 'NO_SAFE_BUILD_SITE', searchRadius: radius, footprint, maxFillDepth }
  return {
    ok: true,
    home: best.home,
    footprint,
    terrain: 'plains_like',
    maxFillDepth,
    digBlocks: best.report.digBlocks,
    fillBlocks: best.report.fillBlocks,
    preferredColumns: best.report.preferred,
    totalColumns: best.report.total,
    score: Math.round(best.score * 100) / 100,
  }
}

export { inspectSite as inspectBuildSite }

/** @param {{delta:number}[]} columns @param {number} homeY */
function totalDelta (columns, homeY) {
  return columns.reduce((sum, column) => sum + Math.abs(column.delta), 0) / Math.max(1, columns.length)
}

/** @param {import('mineflayer').Bot} bot @param {Coord} home */
function reachableBuildSite (bot, home) {
  const pathfinder = bot.pathfinder
  const position = bot.entity?.position
  if (!pathfinder || typeof pathfinder.getPathTo !== 'function' || !pathfinder.movements || !position) return true
  const anchors = [
    { x: home.x + 3, y: home.y, z: home.z - 2 },
    { x: home.x + 3, y: home.y, z: home.z + FOOTPRINT + 1 },
    { x: home.x - 2, y: home.y, z: home.z + 3 },
    { x: home.x + FOOTPRINT + 1, y: home.y, z: home.z + 3 },
  ]
  for (const anchor of anchors) {
    if (Math.hypot(anchor.x - position.x, anchor.z - position.z) <= 3 && Math.abs(anchor.y - position.y) <= 2) return true
    try {
      const result = pathfinder.getPathTo(
        pathfinder.movements,
        new goals.GoalNear(anchor.x, anchor.y, anchor.z, 2),
        500,
      )
      if (result?.status === 'success' && (result.path?.length ?? 0) > 0) return true
    } catch { /* an unavailable pathfinder is handled by the action phase */ }
  }
  return false
}

/** @param {import('mineflayer').Bot} bot @param {number} needed */
function chooseFillMaterial (bot, needed) {
  const priority = ['dirt', 'cobblestone', 'stone']
  for (const name of priority) {
    const total = bot.inventory.items().filter(item => item.name === name).reduce((sum, item) => sum + item.count, 0)
    if (total >= needed) return { name, total }
  }
  return null
}

/** @param {Context} ctx @param {import('mineflayer').Bot} bot @param {Coord} home @param {number} footprint @param {number} maxFillDepth */
async function prepare (ctx, bot, home, footprint, maxFillDepth) {
  const report = inspectSite(bot, home, footprint, maxFillDepth)
  if (!report.ok) throw new ToolError('BUILD_SITE_BLOCKED', `Build site rejected: ${report.reason}. Choose another site.`)
  const material = chooseFillMaterial(bot, report.fillBlocks)
  if (report.fillBlocks && !material) {
    throw new ToolError('BUILD_SITE_MISSING_FILL', `Build site needs ${report.fillBlocks} dirt/cobblestone/stone blocks before leveling.`)
  }
  const handle = ctx.locks.begin('build_site', () => {
    try { bot.pathfinder.stop(); bot.stopDigging() } catch { /* cleanup is best effort */ }
  })
  let dug = 0
  let filled = 0
  try {
    for (const column of report.columns) {
      if (handle.signal.aborted) throw new ToolError('CANCELLED', 'Build-site preparation was cancelled.')
      const desired = home.y - 1
      if (column.delta > 0) {
        for (let y = column.groundY; y > desired; y--) {
          const block = bot.blockAt(new Vec3(column.x, y, column.z))
          if (!safeTerrain(block)) throw new ToolError('BUILD_SITE_BLOCKED', `Unsafe block at ${column.x},${y},${column.z}; refuse to dig it.`)
          await bot.pathfinder.goto(new goals.GoalNear(column.x, y, column.z, 3))
          await withTimeout(bot.dig(block), DEFAULT_ACTION_TIMEOUT_MS, 'build-site dig')
          dug++
        }
      } else if (column.delta < 0 && material) {
        for (let y = column.groundY + 1; y <= desired; y++) {
          const target = bot.blockAt(new Vec3(column.x, y, column.z))
          if (!air(target)) throw new ToolError('BUILD_SITE_BLOCKED', `Target cell ${column.x},${y},${column.z} is occupied.`)
          const support = bot.blockAt(new Vec3(column.x, y - 1, column.z))
          if (!solid(support)) throw new ToolError('BUILD_SITE_NO_SUPPORT', `Cannot fill ${column.x},${y},${column.z} without a solid support.`)
          const item = bot.inventory.items().find(entry => entry.name === material.name)
          if (!item) throw new ToolError('BUILD_SITE_MISSING_FILL', `Fill material ${material.name} ran out.`)
          await bot.pathfinder.goto(new goals.GoalNear(column.x, y, column.z, 3))
          await bot.equip(item.type, 'hand')
          await withTimeout(bot.placeBlock(support, new Vec3(0, 1, 0)), DEFAULT_ACTION_TIMEOUT_MS, 'build-site fill')
          filled++
        }
      }
    }
    const verified = inspectSite(bot, home, footprint, 0)
    if (!verified.ok || verified.columns.some(column => column.groundY !== home.y - 1)) {
      throw new ToolError('BUILD_SITE_VERIFY_FAILED', 'Leveling finished without a continuous solid foundation.')
    }
    return { ok: true, home, footprint, dug, filled, material: material?.name ?? null, verified: true }
  } finally {
    handle.release()
  }
}

export { prepare as prepareBuildSite }

/** @param {Coord} home @param {'foundation'|'walls'|'roof'} phase */
function templateTargets (home, phase) {
  const out = []
  const unique = () => Array.from(new Map(out.map(target => [`${target.x},${target.y},${target.z}`, target])).values())
  const minX = home.x; const maxX = home.x + FOOTPRINT - 1
  const minZ = home.z; const maxZ = home.z + FOOTPRINT - 1
  if (phase === 'foundation') {
    for (let x = minX; x <= maxX; x++) for (let z = minZ; z <= maxZ; z++) out.push({ x, y: home.y, z })
    return unique()
  }
  if (phase === 'walls') {
    for (let y = home.y + 1; y <= home.y + 3; y++) {
      for (let x = minX; x <= maxX; x++) for (const z of [minZ, maxZ]) {
        if (z === maxZ && x === home.x + 3 && y <= home.y + 2) continue
        out.push({ x, y, z })
      }
      for (let z = minZ + 1; z < maxZ; z++) for (const x of [minX, maxX]) out.push({ x, y, z })
    }
    return unique()
  }
  for (let layer = 0; layer <= Math.floor(FOOTPRINT / 2); layer++) {
    const left = minX + layer; const right = maxX - layer
    const north = minZ + layer; const south = maxZ - layer
    for (let x = left; x <= right; x++) for (const z of [north, south]) out.push({ x, y: home.y + 4, z })
    for (let z = north + 1; z < south; z++) for (const x of [left, right]) out.push({ x, y: home.y + 4, z })
  }
  return unique()
}

/** @param {import('mineflayer').Bot} bot @param {string} name */
function inventoryCount (bot, name) {
  return bot.inventory.items().filter(item => item.name === name).reduce((sum, item) => sum + item.count, 0)
}

/** @param {import('mineflayer').Bot} bot @param {number} needed */
function chooseTemplateMaterial (bot, needed) {
  const available = Object.fromEntries(STRUCTURAL_PLANKS.map(name => [name, inventoryCount(bot, name)]).filter(([, count]) => count))
  for (const name of STRUCTURAL_PLANKS) {
    const count = inventoryCount(bot, name)
    if (count >= needed) return { name, count, available }
  }
  return { name: 'oak_planks', count: inventoryCount(bot, 'oak_planks'), available }
}

/** @param {import('../context.js').Context} ctx @param {import('mineflayer').Bot} bot @param {Coord} home @param {'foundation'|'walls'|'roof'} phase */
async function buildTemplate (ctx, bot, home, phase) {
  const targets = templateTargets(home, phase)
  const material = chooseTemplateMaterial(bot, targets.length)
  const missing = targets.filter(target => bot.blockAt(new Vec3(target.x, target.y, target.z))?.name !== material.name)
  if (material.count < missing.length) {
    throw new ToolError('BUILD_MISSING_MATERIALS', `starter_cabin_v1 ${phase} needs ${missing.length} ${material.name}; available ${material.count}.`,
      [`Prepare ${missing.length} ${material.name} before construction. Dirt is never accepted as a structural material.`, JSON.stringify(material.available ?? {})])
  }
  const handle = ctx.locks.begin('build_template', () => {
    try { bot.pathfinder.stop(); bot.stopDigging() } catch { /* cleanup is best effort */ }
  })
  let placed = 0; let replaced = 0
  try {
    for (const target of targets) {
      if (handle.signal.aborted) throw new ToolError('CANCELLED', 'Template construction was cancelled.')
      let block = bot.blockAt(new Vec3(target.x, target.y, target.z))
      if (block?.name === material.name) continue
      if (block && !REPLACEABLE_TEMPLATE_BLOCKS.has(block.name)) {
        throw new ToolError('BUILD_TEMPLATE_OCCUPIED', `Template cell ${target.x},${target.y},${target.z} contains ${block.name}; refusing to overwrite it.`)
      }
      if (block && block.name !== 'air') {
        await bot.pathfinder.goto(new goals.GoalNear(target.x, target.y, target.z, 3))
        await withTimeout(bot.dig(block), DEFAULT_ACTION_TIMEOUT_MS, 'template cleanup')
        replaced++
      }
      const faces = [[0, -1, 0], [0, 1, 0], [-1, 0, 0], [1, 0, 0], [0, 0, -1], [0, 0, 1]]
      let reference = null; let face = null
      for (const [dx, dy, dz] of faces) {
        const candidate = bot.blockAt(new Vec3(target.x + dx, target.y + dy, target.z + dz))
        if (solid(candidate)) { reference = candidate; face = new Vec3(-dx, -dy, -dz); break }
      }
      if (!reference || !face) throw new ToolError('BUILD_TEMPLATE_NO_SUPPORT', `No solid support for ${target.x},${target.y},${target.z}. Prepare the site first.`)
      await bot.pathfinder.goto(new goals.GoalNear(target.x, target.y, target.z, 3))
      block = bot.blockAt(new Vec3(target.x, target.y, target.z))
      if (!air(block)) throw new ToolError('BUILD_TEMPLATE_OCCUPIED', `Template cell ${target.x},${target.y},${target.z} became occupied.`)
      const item = bot.inventory.items().find(entry => entry.name === material.name)
      if (!item) throw new ToolError('BUILD_MISSING_MATERIALS', `Ran out of ${material.name} while building ${phase}.`)
      await bot.equip(item.type, 'hand')
      await withTimeout(bot.placeBlock(reference, face), DEFAULT_ACTION_TIMEOUT_MS, 'template placement')
      const placedBlock = bot.blockAt(new Vec3(target.x, target.y, target.z))
      if (placedBlock?.name !== material.name) throw new ToolError('BUILD_TEMPLATE_VERIFY_FAILED', `Placement at ${target.x},${target.y},${target.z} was not confirmed.`)
      placed++
    }
    return { ok: true, template: 'starter_cabin_v1', phase, material: material.name, placed, replaced, verified: true }
  } finally {
    handle.release()
  }
}

export { buildTemplate as buildStarterCabin }

/** @param {Registrar} reg */
export function registerBuildSite (reg) {
  reg({
    name: 'find_build_site', group: 'build', annotations: { readOnlyHint: true },
    inputSchema: {
      searchRadius: z.number().int().min(8).max(48).optional().describe('Search radius around the bot, default 24'),
      footprint: z.number().int().min(5).max(9).optional().describe('Square house footprint, default 7'),
      maxFillDepth: z.number().int().min(0).max(4).optional().describe('Maximum leveling depth per column, default 2'),
    },
    description: 'Find a plains-like, continuous and walkable house site. Rejects water, deep holes, caves exposed below the footprint, desert-like ground and obstacles. Returns the only home anchor that may be used by the starter cabin template; it never changes blocks.',
    handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      const p = bot.entity.position.floored()
      return findBuildSite(bot, p, args.searchRadius ?? 24, args.footprint ?? FOOTPRINT, args.maxFillDepth ?? 2)
    },
  })
  reg({
    name: 'prepare_build_site', group: 'build', annotations: { destructiveHint: true },
    inputSchema: {
      home: coord.describe('Home anchor returned by find_build_site; this is the future floor Y, not the support block'),
      footprint: z.number().int().min(5).max(9).optional(),
      maxFillDepth: z.number().int().min(0).max(4).optional(),
    },
    description: 'Prepare the exact house footprint before construction: dig only safe natural terrain above the target support, fill low columns with carried dirt/cobblestone/stone, then verify every support cell is solid and level. Refuses caves, unsafe blocks, occupied cells and missing fill material.',
    handler: (args, ctx) => prepare(ctx, ctx.manager.requireBot(), args.home, args.footprint ?? FOOTPRINT, args.maxFillDepth ?? 2),
  })
  reg({
    name: 'build_starter_cabin', group: 'build', annotations: { destructiveHint: true },
    inputSchema: {
      home: coord.describe('Verified home anchor from find_build_site; future floor corner'),
      phase: z.enum(['foundation', 'walls', 'roof']),
    },
    description: 'Deterministically build one starter_cabin_v1 structural phase. Uses only carried wood plank material, preflights the whole phase before placing, replaces only owned natural placeholder blocks, verifies every placement, and refuses dirt as a wall or roof. Prepare missing planks before retrying.',
    handler: (args, ctx) => buildTemplate(ctx, ctx.manager.requireBot(), args.home, args.phase),
  })
}
