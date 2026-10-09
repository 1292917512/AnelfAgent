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
  const best = candidates[0]
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
}
