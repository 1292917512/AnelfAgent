// @ts-check
/** Controlled descent: fall when survivable, otherwise dig a verified support
 * column down to a safe falling height. Player policy: prefer taking fall
 * damage over digging; dig only when the direct fall would kill, one block at
 * a time, never breaking a support whose own support is missing.
 */
import { z } from 'zod'
import { Vec3 } from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import { appendScaffolds } from './anelf-gather-navigation.mjs'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'

const { Movements, goals } = pathfinderPkg

/** @typedef {import('mineflayer').Bot} Bot */

// Landing surfaces the bot must never drop onto.
const HAZARD_LANDINGS = new Set(['lava', 'magma_block', 'campfire', 'soul_campfire', 'fire', 'soul_fire',
  'sweet_berry_bush', 'cactus', 'powder_snow', 'pointed_dripstone', 'cobweb'])
// Keep at least three hearts after the planned fall.
const HEALTH_MARGIN = 6
const MAX_DEPTH = 48
const SETTLE_MS = 3000

/** @param {number} blocks @param {string} landing */
function fallDamage (blocks, landing) {
  if (landing === 'water') return 0
  return Math.max(0, blocks - 3)
}

/** The biome name at the bot's feet, resolved defensively: prismarine-world
 * returns a biome id on most versions.
 * @param {import('mineflayer').Bot} bot @returns {string|null}
 */
export function biomeAt (bot) {
  try {
    const world = bot.world
    if (world == null || typeof world.getBiome !== 'function') return null
    const raw = world.getBiome(bot.entity.position.floored())
    if (raw == null) return null
    if (typeof raw === 'string') return raw
    if (typeof raw === 'object' && typeof raw.name === 'string') return raw.name
    const byId = bot.registry?.biomesById
    if (byId != null && byId[raw]?.name) return byId[raw].name
    const entry = Object.values(bot.registry?.biomesByName ?? {}).find(b => b?.id === raw)
    return entry?.name ?? null
  } catch {
    return null
  }
}

const stairBreakable = name => safety.naturalTerrain.has(name) ||
  /(?:^|_)leaves$/.test(name) || name === 'leaf_litter' ||
  /^(?:red_)?sand(?:stone)?$/.test(name) || /terracotta$/.test(name) ||
  /^(?:cut_|chiseled_)?sandstone$/.test(name)

/** Nearest cell the bot could walk onto without digging or placing: a
 * standable column top with headroom whose neighbourhood is open too —
 * the natural exit a staircase should aim for.
 * @param {Bot} bot @param {number} [maxRadius] @returns {{x:number,y:number,z:number}|null}
 */
export function findWalkableExit (bot, maxRadius = 24) {
  const feet = bot.entity.position.floored()
  const solidAt = p => {
    const block = bot.blockAt(p)
    return block != null && block.boundingBox !== 'empty' && !stairBreakable(block.name) === false
  }
  const standable = p => {
    const floor = bot.blockAt(p.offset(0, -1, 0))
    const at = bot.blockAt(p)
    const head = bot.blockAt(p.offset(0, 1, 0))
    return floor != null && floor.boundingBox === 'block' &&
      at != null && at.boundingBox === 'empty' && head != null && head.boundingBox === 'empty'
  }
  const openArea = p => standable(p) && [[1, 0], [-1, 0], [0, 1], [0, -1]]
    .some(([dx, dz]) => standable(p.offset(dx, 0, dz)))
  let best = null
  let bestDistance = Infinity
  for (let dx = -maxRadius; dx <= maxRadius; dx++) for (let dz = -maxRadius; dz <= maxRadius; dz++) {
    if (Math.abs(dx) <= 1 && Math.abs(dz) <= 1) continue
    const columnBase = new Vec3(feet.x + dx, 0, feet.z + dz)
    // Topmost solid cell in the loaded band around the bot's level.
    let top = null
    // Scan the full height to the surface: deep canyons and mesas hide their
    // lips far above a narrow band, and the standability check filters walls
    // regardless of height.
    for (let y = feet.y + 24; y >= feet.y - 4; y--) {
      const cell = columnBase.offset(0, y, 0)
      const block = bot.blockAt(cell)
      if (block != null && block.boundingBox !== 'empty') { top = cell.offset(0, 1, 0); break }
    }
    if (top == null || top.y <= feet.y + 1 || !openArea(top)) continue
    // An exit must be a lip above the feet: the pocket's own floor is
    // standable but is where the bot already stands, not a way out.
    const distance = feet.distanceTo(top)
    if (distance < bestDistance) { best = { x: top.x, y: top.y, z: top.z }; bestDistance = distance }
  }
  return best
}

/** Deterministic ascending staircase: dig one-up-one-forward steps toward a
 * pre-scanned walkable exit — no pathfinder guesswork, no material needed
 * for the climb itself (each step's floor is natural terrain).
 * @param {Bot} bot @param {AbortSignal} signal @param {{x:number,y:number,z:number}} exit
 */
/** Walk one diagonal-up step into a freshly opened cell with raw physics:
 * face it, forward + jump, bounded by four seconds. No pathfinder involved.
 * @param {Bot} bot @param {AbortSignal} signal @param {Vec3} cell
 * @returns {Promise<boolean>} reached */
async function stepOnto (bot, signal, cell) {
  const centre = cell.clone().offset(0.5, 0.5, 0.5)
  try { await bot.lookAt(centre, true) } catch { /* best effort */ }
  bot.setControlState('forward', true)
  bot.setControlState('jump', true)
  const deadline = Date.now() + 4000
  try {
    while (Date.now() < deadline) {
      signal.throwIfAborted()
      await new Promise(resolve => setTimeout(resolve, 100))
      if (bot.entity.position.floored().equals(cell)) return true
    }
    return false
  } finally {
    bot.setControlState('forward', false)
    bot.setControlState('jump', false)
  }
}

export async function staircaseEscape (bot, signal, exit, maxSteps = 2) {
  signal.throwIfAborted()
  const start = bot.entity.position.floored()
  if (exit.y < start.y) throw new ToolError('NO_CLIMB', 'The exit is below the bot; no staircase needed.')
  // A level exit is a flat corridor: steps advance horizontally with the
  // rise clamped, carving headroom only — the walk to the lip after the
  // climb is part of the same staircase.
  const totalRise = exit.y - start.y
  // Cardinal staircase: one axis per step (a true staircase shape). Diagonal
  // up-steps are blocked by Minecraft's corner-collision rule inside narrow
  // pockets — a player walks stairs one direction at a time too.
  const horizontal = Math.abs(exit.x - start.x) + Math.abs(exit.z - start.z)
  const steps = Math.min(Math.max(totalRise, horizontal), maxSteps)
  let dug = 0
  let cursorX = start.x
  let cursorZ = start.z
  let reverseX = 0
  let reverseZ = 0
  for (let i = 1; i <= steps; i++) {
    signal.throwIfAborted()
    const feetY = start.y + Math.min(i, totalRise)
    const rx = exit.x - cursorX
    const rz = exit.z - cursorZ
    if (rx !== 0 && (Math.abs(rx) >= Math.abs(rz) || rz === 0)) { cursorX += Math.sign(rx); reverseX = -Math.sign(rx); reverseZ = 0 }
    else if (rz !== 0) { cursorZ += Math.sign(rz); reverseZ = -Math.sign(rz); reverseX = 0 }
    const cell = new Vec3(cursorX, feetY, cursorZ)
    // Carve bottom-up: each removed cell exposes the one above to the
    // eye, keeping every dig face visible (top-down from below leaves the
    // upper cells sight-blocked behind the undug middle). The column is
    // forward of the bot, never underfoot. Falling sand refills get
    // re-dug (bounded). Anything man-made aborts the whole climb.
    for (let y = 0; y <= 2; y++) {
      // Sand above the headroom falls into freshly dug cells; each refill is
      // re-dug — desert stacks run deep, so the budget is generous.
      for (let attempt = 0; attempt < 8; attempt++) {
        signal.throwIfAborted()
        const block = bot.blockAt(cell.offset(0, y, 0))
        if (block == null || block.boundingBox === 'empty') break
        if (!stairBreakable(block.name)) {
          throw new ToolError('OBSTRUCTION', `Unbreakable ${block.name} in the staircase at ${cell.offset(0, y, 0)}.`)
        }
        // Hand-mined stone races the server's own dig timer: an
        // unconfirmed finish is a retry, not a failure — progress persists.
        try {
          await safety.digVerified(bot, block, { watchdogMs: 15000, harvest: false })
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          if (!/confirm|timeout/i.test(message)) throw error
          if (bot.blockAt(cell.offset(0, y, 0))?.boundingBox === 'empty') break
          continue
        }
        dug++
        if (bot.blockAt(cell.offset(0, y, 0))?.boundingBox === 'empty') break
      }
      if (bot.blockAt(cell.offset(0, y, 0))?.boundingBox !== 'empty') {
        throw new ToolError('UNCONFIRMED', `Staircase cell ${cell.offset(0, y, 0)} would not open.`)
      }
    }
    // The floor below the new feet must be solid: nature provides it in a
    // staircase; an overhang gap gets bridged with carried dirt when carried.
    // With no material, sidestep instead of aborting — the route bends around
    // gaps (often our own earlier headroom) and the chunk ends with its real
    // progress so the next rescan picks a fresh line.
    const floor = bot.blockAt(cell.offset(0, -1, 0))
    if (floor == null || floor.boundingBox !== 'block') {
      const dirt = bot.inventory?.items?.().find(entry => entry.name === 'dirt')
      if (dirt && bot.placeBlock) {
        try {
          await bot.equip(dirt, 'hand')
          signal.throwIfAborted()
          await bot.placeBlock(bot.blockAt(cell.offset(0, -2, 0)), new Vec3(0, 1, 0))
        } catch { break }
      } else {
        // The forward cell floats over our own earlier carve. Solid wall in
        // ANY direction provides natural floors at every level — reverse the
        // horizontal march (behind us is uncarved terrain more often than
        // not) and keep climbing through it; any surface beats a freeze on
        // the pinnacle.
        reverseX = -reverseX
        reverseZ = -reverseZ
        cursorX += reverseX
        cursorZ += reverseZ
        continue
      }
    }
    // Last sweep: sand that fell after the per-cell loops clears or aborts.
    for (let y = 0; y <= 2; y++) {
      const block = bot.blockAt(cell.offset(0, y, 0))
      if (block == null || block.boundingBox === 'empty' || !stairBreakable(block.name)) continue
      await safety.digVerified(bot, block, { watchdogMs: 15000, harvest: false })
      dug++
    }
    // Step onto it with raw physics — face the cell, walk forward with jump
    // held, exactly like a player. The pathfinder's goto silently resolved
    // without movement in some stopped states; physics has no such mode.
    const stepped = await stepOnto(bot, signal, cell)
    if (!stepped) {
      throw new ToolError('STEP_FAILED', `Could not step onto ${cell} within the bounded window.`)
    }
  }
  const to = Math.floor(bot.entity.position.y)
  const done = to >= exit.y - 1 || Math.floor(bot.entity.position.x) === exit.x && Math.floor(bot.entity.position.z) === exit.z && to >= exit.y - 1
  return { completed: done, climbed: to - start.y, dug, from: start.y, to, exit,
    position: { x: bot.entity.position.x, y: to, z: bot.entity.position.z } }
}

/** Place a carried block-type item (bed, dirt, ...) on the flat ground in
 * front of the bot. @param {Bot} bot @param {AbortSignal} signal @param {string} item */
async function placeItem (bot, signal, item) {
  signal.throwIfAborted()
  const carried = bot.inventory?.items?.().find(entry => entry.name === item)
  if (!carried) throw new ToolError('NO_MATERIAL', `No carried ${item} to place.`)
  await bot.equip(carried, 'hand')
  signal.throwIfAborted()
  const yaw = bot.entity.yaw ?? 0
  const facing = new Vec3(Math.round(-Math.sin(yaw)), 0, Math.round(-Math.cos(yaw)))
  const feet = bot.entity.position.floored()
  const target = feet.offset(facing.x, 0, facing.z)
  const reference = bot.blockAt(target.offset(0, -1, 0))
  const occupied = bot.blockAt(target)
  if (!reference || reference.boundingBox !== 'block' || !occupied || occupied.boundingBox !== 'empty') {
    throw new ToolError('NO_PLACEMENT', 'No flat, open ground in front of the bot.')
  }
  await bot.placeBlock(reference, new Vec3(0, 1, 0))
  signal.throwIfAborted()
  const placed = bot.blockAt(target)
  if (!placed || placed.boundingBox === 'empty') {
    throw new ToolError('PLACEMENT_UNCONFIRMED', `${item} placement was not confirmed.`)
  }
  return { placed: item, position: { x: target.x, y: target.y, z: target.z } }
}

/** @param {import('./registry.js').Registrar} reg */
export function registerBiomeProbe (reg) {
  reg({ name: 'get_biome', group: 'state',
    description: "Read-only: the biome name at the bot's current position (e.g. plains, desert, cherry_grove), or null when the world does not expose biome data.",
    inputSchema: {}, annotations: { readOnlyHint: true, title: 'Current biome' },
    handler: (_args, ctx) => ({ biome: biomeAt(ctx.manager.requireBot()) }) })
}

/** @param {import('./registry.js').Registrar} reg */
export function registerStaircase (reg) {
  reg({ name: 'ascend_stairs', group: 'movement',
    description: "Autonomous routine only: scan for the nearest walkable exit (standable, headroom, open neighbourhood — reachable without digging), then carve a deterministic one-up-one-forward staircase through natural terrain toward it and walk up step by step. Needs no carried material unless a step's floor is missing; refuses man-made blocks.",
    inputSchema: { exit: z.object({ x: z.number().int(), y: z.number().int(), z: z.number().int() }).optional(),
      maxSteps: z.number().int().min(1).max(4).optional().describe('Steps carved per call (hand mining is slow; chunked calls stay under RPC budgets). Default 2.') },
    annotations: { title: 'Staircase escape' },
    handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Staircase escape requires the installed action controller.')
      const exit = args.exit ?? findWalkableExit(bot)
      if (exit == null) throw new ToolError('NO_EXIT', 'No walkable exit within reach; nothing to build a staircase toward.')
      const handle = ctx.locks.begin('ascend_stairs')
      const promise = (async () => staircaseEscape(bot, handle.signal, exit, args.maxSteps ?? 2))()
        .then(result => {
          console.error('[anelf] staircase chunk:', JSON.stringify(result),
            'botAt', JSON.stringify(bot.entity.position))
          return result
        }).catch(error => {
        console.error('[anelf] staircase escape failed at', JSON.stringify(exit),
          String(error?.message ?? error), (error?.stack ?? '').split(String.fromCharCode(10)).slice(0, 8).join(' | '))
        throw error
      })
      promise.then(() => handle.release('completed', ''), error => {
        handle.release(error instanceof ToolError ? error.code : 'blocked', String(error?.message ?? error))
      }).catch(() => {})
      return promise
    } })
}

/** @param {import('./registry.js').Registrar} reg */
export function registerPlaceItem (reg) {
  reg({ name: 'place_item', group: 'movement',
    description: 'Autonomous routine only: place a carried block-type item (a bed, dirt, ...) on the flat ground directly in front of the bot. Placement is verified by the server; returns the placed cell.',
    inputSchema: { item: z.string().regex(/^[a-z0-9_]+$/) }, annotations: { title: 'Place carried item' },
    handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Placement requires the installed action controller.')
      const handle = ctx.locks.begin('place_item')
      const promise = (async () => placeItem(bot, handle.signal, args.item))()
      promise.then(() => handle.release('completed', ''), error => {
        handle.release(error instanceof ToolError ? error.code : 'blocked', String(error?.message ?? error))
      }).catch(() => {})
      return promise
    } })
}

/** @param {import('./registry.js').Registrar} reg */
export function registerAscend (reg) {
  reg({ name: 'ascend', group: 'movement',
    description: 'Autonomous routine only: escape dug terrain. With targetY, pillar up on carried dirt or cobblestone. With targetX/targetZ, break-out walk to that column — carving through natural terrain only, placing scaffold blocks where a step needs one; buildings, containers and floor protection stay untouchable. Every placed block is verified.',
    inputSchema: { targetY: z.number().int().optional(),
      targetX: z.number().int().optional().describe('Break-out walk target column; requires targetZ.'),
      targetZ: z.number().int().optional().describe('Break-out walk target column; requires targetX.') },
    annotations: { title: 'Escape dug terrain' },
    handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Ascend requires the installed action controller.')
      const handle = ctx.locks.begin('ascend')
      const report = ctx.manager.statusReport()
      const world = JSON.stringify([report.host, report.port, bot.username, bot.game.dimension])
      const promise = (async () => ascend(bot, handle.signal, args.targetY, args.targetX, args.targetZ, world))()
      promise.then(() => handle.release('completed', ''), error => {
        handle.release(error instanceof ToolError ? error.code : 'blocked', String(error?.message ?? error))
      }).catch(() => {})
      return promise
    } })
}

/** Record placements that still exist in the world so the persisted
 * scaffold ledger owns their cleanup; blocks already consumed by the descent
 * itself are skipped.
 * @param {Bot} bot @param {string} [world] @param {{position:import('vec3').Vec3,type:number}[]} placed */
async function ledgerPlacements (bot, world, placed) {
  if (!world || !placed.length) return
  const surviving = placed.filter(record => {
    const block = bot.blockAt(record.position)
    return block != null && block.boundingBox !== 'empty' && block.type === record.type
  })
  if (surviving.length) await appendScaffolds(world, surviving)
}

/** @param {Bot} bot @param {AbortSignal} signal @param {number} [targetY]
 * @param {number} [targetX] @param {number} [targetZ] @param {string} [world] */
export async function ascend (bot, signal, targetY, targetX, targetZ, world) {
  /** Every block the escape places (pillar towers, breakout steps) joins the
   * same cleanup ledger as gather scaffolds. @type {{position:import('vec3').Vec3,type:number}[]} */
  const placedBlocks = []
  const originalPlace = bot.placeBlock
  if (typeof originalPlace === 'function') {
    bot.placeBlock = async (reference, face) => {
      const result = await originalPlace(reference, face)
      try {
        const at = reference.position.plus(face).floored()
        const block = bot.blockAt(at)
        if (block != null && block.boundingBox !== 'empty') {
          placedBlocks.push({ position: at.clone(), type: block.type })
        }
      } catch { /* recording is best effort */ }
      return result
    }
  }
  try {
    return await ascendInner(bot, signal, targetY, targetX, targetZ, world, placedBlocks)
  } finally {
    if (typeof originalPlace === 'function') bot.placeBlock = originalPlace
  }
}

/** @param {Bot} bot @param {AbortSignal} signal @param {number} [targetY]
 * @param {number} [targetX] @param {number} [targetZ] @param {string} [world]
 * @param {{position:import('vec3').Vec3,type:number}[]} placedBlocks */
async function ascendInner (bot, signal, targetY, targetX, targetZ, world, placedBlocks) {
  signal.throwIfAborted()
  const pathfinder = bot.pathfinder
  if (!pathfinder?.goto || typeof pathfinder?.setMovements !== 'function') {
    throw new ToolError('PLUGIN_MISSING', 'The pathfinder plugin is required to pillar out.')
  }
  const placeable = ['dirt', 'cobblestone']
    .map(name => bot.registry.itemsByName[name]?.id)
    .filter(Number.isInteger)
  // The break-out walk carves terrain and needs no carried blocks; only the
  // pillar climb places them.
  if (targetX == null || targetZ == null) {
    const carried = bot.inventory?.items?.().find(entry => placeable.includes(entry.type))
    if (!carried) throw new ToolError('NO_MATERIAL', 'No carried dirt or cobblestone to pillar with; refusing the climb.')
  }
  // Escape profile: pillar with carried blocks and, armed by the safety
  // wrapper, carve through natural terrain only — buildings, containers and
  // floor protection stay untouchable. Wide dug basins have no standable rim
  // to walk to, so digging an ascending exit is the only way out.
  const movements = new Movements(bot)
  movements.anelfEscape = true
  movements.canDig = true
  movements.allow1by1towers = true
  movements.allowParkour = false
  movements.scafoldingBlocks = placeable
  const start = bot.entity.position.floored()
  const from = start.y
  if (targetX != null && targetZ != null) {
    // Break-out walk: same escape profile (carve natural terrain, place
    // scaffold steps), aimed at a far column instead of a height. The
    // sea-level floor applies to horizontal carving only — digging hills
    // into staircases is overland travel, digging under 63 is a gopher
    // tunnel — but only while the bot already stands above sea level. A
    // bot stuck below 63 (empty inventory, no pillar material) must be
    // allowed to carve its way up through anything; forbidding the climb
    // out is a prison, not a policy.
    if (bot.entity.position.y >= 64) {
      movements.exclusionAreasStep.push(block => (block.position.y < 64 ? 100 : 0))
    }
    pathfinder.setMovements(movements)
    const timer = setTimeout(() => pathfinder.stop(), 90000)
    try {
      await pathfinder.goto(new goals.GoalNear(targetX, from, targetZ, 4))
    } finally {
      clearTimeout(timer)
    }
    const to = Math.floor(bot.entity.position.y)
    const reached = Math.hypot(bot.entity.position.x - targetX - 0.5, bot.entity.position.z - targetZ - 0.5) <= 6
    return { completed: reached, from, to, position: { x: bot.entity.position.x, y: to, z: bot.entity.position.z } }
  }
  // Surrounding terrain top (standing height) from a ring scan: in a dug pit
  // the ring is the rim, and pillaring exactly to it ends standing on the
  // surface. An "adjacent standable cell" check is useless here — the pit
  // floor itself is standable.
  const isTreePart = name => /(?:^|_)leaves$/.test(name) || name === 'leaf_litter' || /_log$/.test(name)
  const rimTop = () => {
    let top = -Infinity
    // Chunk pits are up to 16x16: a tight ring would still be inside one.
    // Tree crowns are walkable for the pathfinder but they are not terrain:
    // targeting them sends the escape up a tree instead of out of the pit.
    for (let dx = -8; dx <= 8; dx++) for (let dz = -8; dz <= 8; dz++) {
      if (Math.abs(dx) <= 1 && Math.abs(dz) <= 1) continue
      for (let y = from + 24; y >= from - 4; y--) {
        const block = bot.blockAt(new Vec3(start.x + dx, y, start.z + dz))
        if (block != null && block.boundingBox !== 'empty') {
          if (!isTreePart(block.name)) top = Math.max(top, y + 1)
          break
        }
      }
    }
    return top
  }
  const rim = rimTop()
  const maxY = Math.min(targetY ?? rim, from + 24)
  if (maxY <= from + 1) {
    // Nothing to climb: the surroundings do not rise above the feet. Reporting
    // failure lets the caller fall back to its cooldown instead of livelocking
    // on no-op climbs.
    return { completed: false, from, to: from, rim: from,
      position: { x: start.x, y: from, z: start.z } }
  }
  const goal = new goals.Goal()
  goal.isEnd = node => node.y >= maxY
  goal.heuristic = node => Math.max(0, maxY - node.y)
  pathfinder.setMovements(movements)
  // pathfinder.goto has no wall-clock bound of its own; stop it hard.
  const timer = setTimeout(() => pathfinder.stop(), 90000)
  try {
    await pathfinder.goto(goal)
  } finally {
    clearTimeout(timer)
  }
  const to = Math.floor(bot.entity.position.y)
  await ledgerPlacements(bot, world, placedBlocks)
  return { completed: to >= maxY, from, to, rim, position: { x: bot.entity.position.x, y: to, z: bot.entity.position.z } }
}

/** @param {import('./registry.js').Registrar} reg */
export function registerDescent (reg) {
  reg({ name: 'descend', group: 'movement',
    description: 'Autonomous routine only: controlled descent from an elevated stranded spot. ' +
      'A survivable drop (water landing or enough remaining health, at least three blocks of fall) ' +
      'is taken directly by breaking the support. Otherwise the bot digs straight down one block ' +
      'at a time — bridging an open shaft first by placing carried dirt against the support underside, ' +
      'so every step lands exactly one block with zero fall damage — until the target height is ' +
      'reached or the remaining fall turns survivable. ' +
      'Hazardous landings (lava, fire, spikes) are refused.',
    inputSchema: { targetY: z.number().int().optional() }, annotations: { title: 'Controlled descent' },
    handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Descent requires the installed action controller.')
      const handle = ctx.locks.begin('descend')
      const report = ctx.manager.statusReport()
      const world = JSON.stringify([report.host, report.port, bot.username, bot.game.dimension])
      const promise = (async () => descend(bot, handle.signal, args.targetY, world))()
      promise.then(() => handle.release('completed', ''), error => {
        handle.release(error instanceof ToolError ? error.code : 'blocked', String(error?.message ?? error))
      }).catch(() => {})
      return promise
    } })
}

/** @param {Bot} bot @param {AbortSignal} signal @param {number} [targetY] @param {string} [world] */
export async function descend (bot, signal, targetY, world) {
  /** @type {{position:import('vec3').Vec3,type:number}[]} */
  const placedBlocks = []
  signal.throwIfAborted()
  const start = bot.entity.position.floored()
  /** First non-empty cell below the feet, skipping the immediate support:
   * that support is what gets broken, never the landing. Fluids are
   * collision-less in the registry, so hazards (lava) and water end the scan
   * as landings in their own right instead of being skipped like air on the
   * way to the pool floor. Unloaded depth is not evaluated.
   * @returns {{y:number,name:string}|null} */
  const scanLanding = () => {
    const feet = bot.entity.position.floored()
    for (let dy = 2; dy <= MAX_DEPTH; dy++) {
      const block = bot.blockAt(feet.offset(0, -dy, 0))
      if (!block) return null
      if (HAZARD_LANDINGS.has(block.name) || block.name === 'water') {
        return { y: feet.y - dy, name: block.name }
      }
      if (block.boundingBox === 'empty') continue
      return { y: feet.y - dy, name: block.name }
    }
    return null
  }
  /** The cell actually holding the bot up. Standing on a block edge floors the
   * feet cell onto a neighbour with air below, so scan the 3×3 ring under the
   * feet and take the solid cell nearest the bot's centre.
   * @param {Vec3} feet @returns {import('mineflayer').Block|null} */
  const supportUnder = feet => {
    const center = bot.entity.position
    let best = null
    let bestDistance = Infinity
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      const cell = new Vec3(feet.x + dx, feet.y - 1, feet.z + dz)
      const block = bot.blockAt(cell)
      if (!block || block.boundingBox === 'empty') continue
      const distance = center.distanceTo(new Vec3(cell.x + 0.5, cell.y + 1, cell.z + 0.5))
      if (distance < bestDistance) { best = block; bestDistance = distance }
    }
    return best
  }
  let dug = 0
  let fell = false
  for (;;) {
    signal.throwIfAborted()
    if (!bot.entity.onGround) throw new ToolError('AIRBORNE', 'Left the ground mid-descent; stopping.')
    const feet = bot.entity.position.floored()
    if (targetY != null && feet.y <= targetY + 2) {
      // Target height reached: ordinary work can take it from here.
      await ledgerPlacements(bot, world, placedBlocks)
      return { completed: true, fallen: fell, dug, from: start.y, to: feet.y,
        expectedDamage: 0, position: { x: feet.x, y: feet.y, z: feet.z } }
    }
    // Every fall or dig reshaped the column below: rescan so the damage and
    // hazard verdicts describe the actual next drop, never the first landing.
    const landing = scanLanding()
    if (landing == null) {
      // Nothing solid below within the scan budget: this is the deepest
      // standable cell. Without a target that is a complete descent; with an
      // unreachable target it is an honest refusal.
      if (targetY == null) {
        await ledgerPlacements(bot, world, placedBlocks)
        return { completed: true, fallen: fell, dug, from: start.y, to: feet.y,
          expectedDamage: 0, position: { x: feet.x, y: feet.y, z: feet.z } }
      }
      throw new ToolError('NO_LANDING', 'No loaded landing below; refusing blind descent.')
    }
    if (HAZARD_LANDINGS.has(landing.name)) throw new ToolError('UNSAFE_LANDING', `Landing is ${landing.name}; refusing.`)
    const health = bot.health
    if (!(health > 0)) throw new ToolError('UNHEALTHY', 'Health unknown or zero; refusing a planned fall.')
    const damage = fallDamage(feet.y - landing.y, landing.name)
    const support = supportUnder(feet)
    if (!support) {
      throw new ToolError('NO_SUPPORT', 'Nothing solid under the feet; descent has nothing to break.')
    }
    if (damage < health - HEALTH_MARGIN) {
      // Prefer taking the hit: any non-lethal drop breaks its support for a
      // free fall — even one block left above ground, never digging through
      // the floor just to save that single fall.
      await safety.breakForFall(bot, support, signal)
      dug++
      fell = true
      await settle(bot, signal, feet.y)
      continue
    }
    // The fall would kill: dig straight down one block at a time. Before each
    // dig the cell two below must be solid — when nature did not provide one,
    // bridge it by placing carried dirt against the support underside (the
    // player trick), so every step drops exactly one block with zero damage.
    const underSupport = bot.blockAt(feet.offset(0, -2, 0))
    if (!underSupport || underSupport.boundingBox !== 'block') {
      const dirt = bot.inventory?.items?.().find(entry => entry.name === 'dirt')
      if (!dirt || !bot.placeBlock) {
        throw new ToolError('OPEN_SHAFT', 'No support below and no carried dirt to bridge one; refusing an uncontrolled fall.')
      }
      await bot.equip(dirt, 'hand')
      signal.throwIfAborted()
      await bot.placeBlock(support, new Vec3(0, -1, 0))
      const bridged = bot.blockAt(feet.offset(0, -2, 0))
      if (bridged?.boundingBox === 'block') placedBlocks.push({ position: bridged.position.clone(), type: bridged.type })
      if (!bridged || bridged.boundingBox !== 'block') {
        throw new ToolError('BRIDGE_FAILED', 'Placing the bridging block under the support was not confirmed.')
      }
    }
    await safety.descendTemporarySupport(bot, support, signal)
    dug++
    if (dug > MAX_DEPTH) throw new ToolError('DEPTH', 'Descent budget exhausted.')
    await settle(bot, signal, feet.y)
  }
}

/** @param {Bot} bot @param {AbortSignal} signal */
/** Settle after a fall or a dig. The dig promise can resolve before the next
 * physics tick, leaving a stale onGround/velocity read that would end the
 * wait before the fall even started; first wait for visible motion (bounded,
 * because breaking an edge support legitimately moves nothing), then for a
 * grounded rest.
 * @param {Bot} bot @param {AbortSignal} signal @param {number} [fromY] */
async function settle (bot, signal, fromY) {
  const startAt = Date.now()
  while (Date.now() - startAt < 600 && bot.entity.onGround &&
      Math.abs(bot.entity.velocity?.y ?? 0) <= 0.1 &&
      (fromY == null || bot.entity.position.y >= fromY - 0.001)) {
    signal.throwIfAborted()
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  const deadline = startAt + SETTLE_MS
  while ((!bot.entity.onGround || Math.abs(bot.entity.velocity?.y ?? 0) > 0.1) && Date.now() < deadline) {
    signal.throwIfAborted()
    await new Promise(resolve => setTimeout(resolve, 50))
  }
}
