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

/** @param {import('./registry.js').Registrar} reg */
export function registerAscend (reg) {
  reg({ name: 'ascend', group: 'movement',
    description: 'Autonomous routine only: pillar straight up on carried dirt or cobblestone to escape a pit the bot dug itself. Placement-only — never digs — and every placed block is verified. Stops at targetY, or earlier when headroom or materials run out.',
    inputSchema: { targetY: z.number().int().optional() }, annotations: { title: 'Pillar escape' },
    handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Ascend requires the installed action controller.')
      const handle = ctx.locks.begin('ascend')
      const promise = (async () => ascend(bot, handle.signal, args.targetY))()
      promise.then(() => handle.release('completed', ''), error => {
        handle.release(error instanceof ToolError ? error.code : 'blocked', String(error?.message ?? error))
      }).catch(() => {})
      return promise
    } })
}

/** @param {Bot} bot @param {AbortSignal} signal @param {number} [targetY] */
export async function ascend (bot, signal, targetY) {
  signal.throwIfAborted()
  const pathfinder = bot.pathfinder
  if (!pathfinder?.goto || typeof pathfinder?.setMovements !== 'function') {
    throw new ToolError('PLUGIN_MISSING', 'The pathfinder plugin is required to pillar out.')
  }
  const placeable = ['dirt', 'cobblestone']
    .map(name => bot.registry.itemsByName[name]?.id)
    .filter(Number.isInteger)
  const carried = bot.inventory?.items?.().find(entry => placeable.includes(entry.type))
  if (!carried) throw new ToolError('NO_MATERIAL', 'No carried dirt or cobblestone to pillar with; refusing the climb.')
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
      const promise = (async () => descend(bot, handle.signal, args.targetY))()
      promise.then(() => handle.release('completed', ''), error => {
        handle.release(error instanceof ToolError ? error.code : 'blocked', String(error?.message ?? error))
      }).catch(() => {})
      return promise
    } })
}

/** @param {Bot} bot @param {AbortSignal} signal @param {number} [targetY] */
export async function descend (bot, signal, targetY) {
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
