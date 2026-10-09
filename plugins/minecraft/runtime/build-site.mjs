// @ts-check
/** Deterministic build-site selection and bounded terrain preparation. */
import { z } from 'zod'
import { Vec3 } from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
const { goals } = pathfinderPkg
import { ToolError } from '../util/errors.js'
import { withTimeout } from '../util/async.js'
import { DEFAULT_ACTION_TIMEOUT_MS } from '../config.js'
import { GatherNavigation } from './anelf-gather-navigation.mjs'
import { GatherTask } from './anelf-gather-task.mjs'
import { findTable, inventoryClean, validateProductionSpace, ProductionTask } from './anelf-production-task.mjs'
import { itemCount, planProduction } from './anelf-production-plan.mjs'

/** @typedef {import('../context.js').Context} Context */
/** @typedef {import('./registry.js').Registrar} Registrar */
/** @typedef {{x:number,y:number,z:number}} Coord */

const coord = z.object({ x: z.number().int(), y: z.number().int(), z: z.number().int() })
const PREFERRED = new Set(['grass_block', 'dirt', 'coarse_dirt', 'podzol', 'mycelium'])
const ACCEPTABLE = new Set([...PREFERRED, 'stone', 'cobblestone', 'deepslate'])
const LIQUID = new Set(['water', 'lava'])
const STRUCTURAL_PLANKS = ['oak_planks', 'birch_planks', 'spruce_planks', 'jungle_planks',
  'acacia_planks', 'dark_oak_planks', 'cherry_planks', 'mangrove_planks']
const LOG_TYPES = STRUCTURAL_PLANKS.map(name => name.replace('_planks', '_log'))
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
async function prepare (ctx, bot, home, footprint, maxFillDepth, parentSignal = null) {
  const report = inspectSite(bot, home, footprint, maxFillDepth)
  if (!report.ok) throw new ToolError('BUILD_SITE_BLOCKED', `Build site rejected: ${report.reason}. Choose another site.`)
  const material = chooseFillMaterial(bot, report.fillBlocks)
  if (report.fillBlocks && !material) {
    throw new ToolError('BUILD_SITE_MISSING_FILL', `Build site needs ${report.fillBlocks} dirt/cobblestone/stone blocks before leveling.`)
  }
  const handle = parentSignal ? null : ctx.locks.begin('build_site', () => {
    try { bot.pathfinder.stop(); bot.stopDigging() } catch { /* cleanup is best effort */ }
  })
  const signal = parentSignal ?? handle.signal
  let dug = 0
  let filled = 0
  try {
    for (const column of report.columns) {
      if (signal.aborted) throw new ToolError('CANCELLED', 'Build-site preparation was cancelled.')
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
    if (handle) handle.release()
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
async function buildTemplate (ctx, bot, home, phase, parentSignal = null) {
  const targets = templateTargets(home, phase)
  const material = chooseTemplateMaterial(bot, targets.length)
  const missing = targets.filter(target => bot.blockAt(new Vec3(target.x, target.y, target.z))?.name !== material.name)
  if (material.count < missing.length) {
    throw new ToolError('BUILD_MISSING_MATERIALS', `starter_cabin_v1 ${phase} needs ${missing.length} ${material.name}; available ${material.count}.`,
      [`Prepare ${missing.length} ${material.name} before construction. Dirt is never accepted as a structural material.`, JSON.stringify(material.available ?? {})])
  }
  const handle = parentSignal ? null : ctx.locks.begin('build_template', () => {
    try { bot.pathfinder.stop(); bot.stopDigging() } catch { /* cleanup is best effort */ }
  })
  const signal = parentSignal ?? handle.signal
  let placed = 0; let replaced = 0
  try {
    for (const target of targets) {
      if (signal.aborted) throw new ToolError('CANCELLED', 'Template construction was cancelled.')
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
    if (handle) handle.release()
  }
}

export { buildTemplate as buildStarterCabin }

/** @param {import('mineflayer').Bot} bot @param {string} logName @param {number} maxDistance */
function woodCandidates (bot, logName, maxDistance) {
  const definition = bot.registry?.blocksByName?.[logName]
  if (!definition || !Number.isInteger(definition.id)) return []
  const center = bot.entity.position.floored()
  return bot.findBlocks({ matching: definition.id, point: center, maxDistance, count: 32 })
    .map(position => ({ position, distance: position.distanceTo(center) }))
    .filter(candidate => {
      const block = bot.blockAt(candidate.position)
      return block?.name === logName && candidate.position.y >= center.y - 3 && candidate.position.y <= center.y + 3
    })
    // Start from the lowest log in a trunk column.  Starting at the top
    // makes the bounded gather task treat the lower trunk as unreachable and
    // can leave a floating tree behind.
    .sort((a, b) => {
      const horizontal = Math.hypot(a.position.x - center.x, a.position.z - center.z) -
        Math.hypot(b.position.x - center.x, b.position.z - center.z)
      return horizontal || a.position.y - b.position.y || a.distance - b.distance
    })
}

/** @param {import('mineflayer').Bot} bot @param {{x:number,y:number,z:number}} target @param {AbortSignal} signal */
async function walkToWood (bot, target, signal) {
  const pathfinder = bot.pathfinder
  const standPoint = new Vec3(target.x, target.y + 1, target.z)
  const targetPoint = new Vec3(target.x, target.y, target.z)
  if (pathfinder?.movements && typeof pathfinder.getPathTo === 'function') {
    const movement = pathfinder.movements
    const priorCanDig = movement.canDig
    movement.canDig = false
    try {
      const goal = new goals.GoalNearXZ(target.x, target.z, 2)
      const result = pathfinder.getPathTo(movement, goal, 1500)
      if (result?.status === 'success' && result.path?.length) {
        signal.throwIfAborted()
        await withTimeout(pathfinder.goto(goal), DEFAULT_ACTION_TIMEOUT_MS * 2, 'walk to authorized tree')
        signal.throwIfAborted()
        const arrived = bot.entity.position.floored()
        if (arrived.y === standPoint.y && arrived.distanceTo(targetPoint) <= 8) return true
      }
    } finally {
      movement.canDig = priorCanDig
    }
  }
  for (let hop = 0; hop < 6; hop++) {
    signal.throwIfAborted()
    const entry = bot.entity.position.floored()
    const distance = Math.hypot(target.x - entry.x, target.z - entry.z)
    if (entry.distanceTo(targetPoint) <= 8 && entry.y === standPoint.y) return true
    const step = Math.max(0, Math.min(8, distance - 6))
    const dx = distance === 0 ? 0 : (target.x - entry.x) / distance
    const dz = distance === 0 ? 0 : (target.z - entry.z) / distance
    const waypointY = standPoint.y < entry.y ? Math.max(standPoint.y, entry.y - 1) : Math.min(standPoint.y, entry.y + 1)
    const levelYs = waypointY === entry.y ? [entry.y] : [entry.y, waypointY]
    const waypoints = levelYs.flatMap(y => [
      new Vec3(Math.round(entry.x + dx * step), y, Math.round(entry.z + dz * step)),
      new Vec3(Math.round(entry.x + dz * step), y, Math.round(entry.z - dx * step)),
      new Vec3(Math.round(entry.x - dz * step), y, Math.round(entry.z + dx * step)),
    ])
    let walked = false
    // Try the least destructive route first, then allow clearing obstacles
    // above the feet when foliage blocks every low-clearance route.
    for (const mode of ['leaf', 'high']) {
      const navigation = new GatherNavigation(bot, entry, signal)
      for (const waypoint of waypoints) {
        if (!navigation.safeRoutePoint(waypoint, mode) || !await navigation.reachable(bot.entity.position, waypoint, mode)) continue
        await navigation.walk(waypoint, mode)
        walked = true
        break
      }
      if (walked) break
    }
    if (!walked) return false
  }
  return false
}

/** @param {import('mineflayer').Bot} bot @param {Coord} home @param {AbortSignal} signal */
async function walkHome (bot, home, signal) {
  const pathfinder = bot.pathfinder
  if (!pathfinder?.movements || typeof pathfinder.getPathTo !== 'function') throw new ToolError('BUILD_NO_RETURN_ROUTE', 'Pathfinder is unavailable for the return to the home site.')
  const target = { x: home.x + 3, y: home.y, z: home.z - 2 }
  const goal = new goals.GoalNear(target.x, target.y, target.z, 2)
  const result = pathfinder.getPathTo(pathfinder.movements, goal, 1000)
  if (result?.status !== 'success' || !(result.path?.length > 0)) throw new ToolError('BUILD_NO_RETURN_ROUTE', 'No confirmed route from the authorized tree area back to the home site.')
  signal.throwIfAborted()
  await withTimeout(pathfinder.goto(goal), DEFAULT_ACTION_TIMEOUT_MS * 2, 'return to home')
  signal.throwIfAborted()
}

/** @param {import('../context.js').Context} ctx @param {import('mineflayer').Bot} bot @param {Coord} home @param {number} footprint @param {number} maxFillDepth @param {number} woodSearchRadius @param {AbortSignal} signal */
async function buildHome (ctx, bot, home, footprint, maxFillDepth, woodSearchRadius, signal) {
  if (!inventoryClean(bot)) throw new ToolError('BUSY', 'Close other windows and recover the inventory before building.')
  const site = await prepare(ctx, bot, home, footprint, maxFillDepth, signal)
  if (typeof bot.waitForChunksToLoad === 'function') await withTimeout(bot.waitForChunksToLoad(), 10000, 'load nearby chunks')
  const requiredPlanks = templateTargets(home, 'foundation').length + templateTargets(home, 'walls').length + templateTargets(home, 'roof').length
  /** @type {{log:string,plank:string}|null} */
  let wood = null
  /** @type {{plan:unknown,table:unknown}|null} */
  let production = null
  const tried = new Set()
  const failures = []
  for (let tree = 0; tree < 12; tree++) {
    signal.throwIfAborted()
    if (wood) {
      const table = findTable(bot)
      try {
        const plan = planProduction(bot, wood.plank, requiredPlanks, 'ensure', Boolean(table))
        validateProductionSpace(bot, plan)
        production = { plan, table }
        break
      } catch (error) {
        if (!(error instanceof ToolError) || error.code !== 'MISSING_MATERIALS') throw error
      }
    }
    const choices = []
    for (const log of LOG_TYPES) for (const candidate of woodCandidates(bot, log, woodSearchRadius)) {
      const key = `${log}:${candidate.position.x},${candidate.position.y},${candidate.position.z}`
      if (!tried.has(key)) choices.push({ log, plank: log.replace('_log', '_planks'), ...candidate, key })
    }
    choices.sort((a, b) => a.distance - b.distance)
    let gathered = false
    for (const choice of choices) {
      tried.add(choice.key)
      if (wood && wood.log !== choice.log) continue
      if (!await walkToWood(bot, choice.position, signal)) {
        failures.push(`${choice.key}: no bounded route`)
        continue
      }
      wood ??= { log: choice.log, plank: choice.plank }
      const entry = bot.entity.position.floored()
      const task = new GatherTask(ctx, bot, { block: wood.log, count: 32, x: entry.x, y: entry.y, z: entry.z, radius: 3, withdraw: [], deposit: false })
      task.start(true); await task.done
      const result = task.snapshot()
      if (result.phase === 'completed' && result.gained > 0) { gathered = true; break }
      failures.push(`${choice.key}: ${result.phase} ${result.reason || 'no inventory gain'}`)
    }
    if (!gathered && !production) {
      throw new ToolError('BUILD_MATERIALS_UNAVAILABLE', 'No reachable complete tree can supply the selected structural plank family within the bounded area.', failures.slice(-8))
    }
  }
  if (!production) throw new ToolError('BUILD_MATERIALS_UNAVAILABLE', `Unable to prepare ${requiredPlanks} matching structural planks within the bounded tree budget.`)
  const craft = new ProductionTask(ctx, bot, production.plan, production.table)
  craft.start(true); await craft.done
  const crafted = craft.snapshot()
  if (crafted.phase !== 'completed' || crafted.available < requiredPlanks || !crafted.inventoryClean) throw new ToolError('BUILD_MATERIALS_UNAVAILABLE', `Structural plank preparation was not confirmed: ${crafted.reason}`)
  await walkHome(bot, home, signal)
  const verified = await prepare(ctx, bot, home, footprint, maxFillDepth, signal)
  const phases = []
  for (const phase of ['foundation', 'walls', 'roof']) phases.push(await buildTemplate(ctx, bot, home, /** @type {'foundation'|'walls'|'roof'} */ (phase), signal))
  return { ok: true, home, site, wood, requiredPlanks, crafted, verified, phases }
}

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
  reg({
    name: 'build_starter_home', group: 'build', annotations: { destructiveHint: true },
    inputSchema: {
      searchRadius: z.number().int().min(8).max(48).optional().describe('Search radius for the home site, default 24'),
      footprint: z.number().int().min(5).max(9).optional().describe('Square house footprint, default 7'),
      maxFillDepth: z.number().int().min(0).max(4).optional().describe('Maximum leveling depth, default 2'),
      woodSearchRadius: z.number().int().min(8).max(32).optional().describe('Bounded search radius for reachable trees, default 32'),
    },
    description: 'Run one deterministic starter_cabin_v1 build: select a reachable plains site, level and verify it, gather complete nearby trees, craft matching planks, return to the same site, then build foundation, walls and roof. The model should call this once instead of orchestrating the individual build and preparation tools. It never uses dirt as structural material, never issues per-block place_block calls, and stops with a concrete error before unsafe or partial phases.',
    handler: async (args, ctx) => {
      const bot = ctx.manager.requireBot()
      const result = findBuildSite(bot, bot.entity.position.floored(), args.searchRadius ?? 24, args.footprint ?? FOOTPRINT, args.maxFillDepth ?? 2)
      if (!result.ok) throw new ToolError('NO_SAFE_BUILD_SITE', 'No reachable plains-like site passed the house safety checks.')
      return buildHome(ctx, bot, result.home, args.footprint ?? FOOTPRINT, args.maxFillDepth ?? 2,
        args.woodSearchRadius ?? 32, ctx.locks.action?.controller.signal ?? new AbortController().signal)
    },
  })
}
