// @ts-check
/** Shared movement and floor protection for every mining entry point. */
const { Movements } = require('mineflayer-pathfinder')

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-block').Block} Block */
/** @type {WeakMap<Bot, Set<string>>} */
const floors = new WeakMap()
/** @type {WeakSet<Bot>} */
const installed = new WeakSet()
/** @type {WeakSet<Movements>} */
const restricted = new WeakSet()
/** @type {WeakMap<Bot, Block>} */
const temporaryDescent = new WeakMap()
/** @type {WeakMap<Bot, Block>} */
const deliberateFall = new WeakMap()

class MiningError extends Error {
  /** @param {string} code @param {string} message */
  constructor (code, message) {
    super(message)
    this.name = 'MiningError'
    this.code = code
  }
}

/** @param {{x:number,y:number,z:number}} p */
function key (p) { return `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}` }

/** Prefer empty hands when a held tool offers no harvesting or speed benefit.
 * @param {Bot & {tool: import('mineflayer-tool').Tool}} bot @param {Block} block
 */
async function equipHarvestTool (bot, block) {
  await bot.tool.equipForBlock(block, { requireHarvest: true, getFromChest: false })
  if (bot.heldItem && block.canHarvest(null) && typeof block.digTime === 'function' &&
      block.digTime(null, false, false, false) <= block.digTime(bot.heldItem.type, false, false, false, bot.heldItem.enchants)) {
    await bot.unequip('hand')
  }
}

/** @param {Movements} movements @returns {Movements} */
function restrictMovements (movements) {
  const gatherMode = movements.anelfGatherMode
  // Foliage-only digging is the one universal travel exception (see
  // installSafety): without it a canopy is a one-way trap — leaves are solid
  // to stand on but were undiggable during plain travel.
  movements.canDig = movements.anelfTravelDig === true ||
    gatherMode === 'leaf' || gatherMode === 'high' || gatherMode === 'scaffold'
  movements.allow1by1towers = gatherMode === 'scaffold'
  movements.allowParkour = false
  movements.allowFreeMotion = false
  movements.allowSprinting = false
  // Pathfinder measures solid landings to the supporting block, one below the feet.
  movements.maxDropDown = 2
  movements.infiniteLiquidDropdownDistance = false
  movements.scafoldingBlocks = gatherMode === 'scaffold' ? [...(movements.anelfScaffoldingBlocks ?? [])] : []
  movements.dontCreateFlow = true
  movements.dontMineUnderFallingBlock = true
  if (!restricted.has(movements)) {
    restricted.add(movements)
    const drop = movements.getMoveDropDown.bind(movements)
    const down = movements.getMoveDown.bind(movements)
    movements.getMoveDropDown = (node, direction, neighbors) => {
      drop(node, direction, neighbors)
      for (let i = neighbors.length - 1; i >= 0; i--) {
        if (node.y - neighbors[i].y > 1) neighbors.splice(i, 1)
      }
    }
    movements.getMoveDown = (node, neighbors) => {
      down(node, neighbors)
      for (let i = neighbors.length - 1; i >= 0; i--) {
        if (node.y - neighbors[i].y > 1) neighbors.splice(i, 1)
      }
    }
  }
  return movements
}

/** @param {Bot} bot @param {{x:number,y:number,z:number}} p */
function protectFloor (bot, p) {
  let protectedFloors = floors.get(bot)
  if (!protectedFloors) { protectedFloors = new Set(); floors.set(bot, protectedFloors) }
  protectedFloors.add(key(p))
}

/** Whether the block is part of the support the bot currently stands on.
 * @param {Bot} bot @param {{x:number,y:number,z:number}} b
 */
function isOwnSupport (bot, b) {
  const p = bot.entity.position
  const half = (bot.entity.width || 0.6) / 2
  return b.y < p.y && b.y + 1 >= p.y - 0.1 &&
    b.x < p.x + half && b.x + 1 > p.x - half &&
    b.z < p.z + half && b.z + 1 > p.z - half
}

/** @param {Bot} bot @param {Block} block */
function assertFloorSafe (bot, block) {
  const p = bot.entity.position
  const b = block.position
  if (temporaryDescent.get(bot) === block && !floors.get(bot)?.has(key(b)) &&
      bot.entity.onGround && Math.abs(p.x - b.x - 0.5) < 0.2 && Math.abs(p.z - b.z - 0.5) < 0.2 &&
      Math.abs(p.y - b.y - 1) < 0.1 && bot.blockAt(b.offset(0, -1, 0))?.boundingBox === 'block') return
  // A deliberate, survivable fall breaks its own support on purpose: grounded
  // anywhere over the block — including an edge stand where the floored feet
  // cell sits beside the real support — is enough; the open drop below is the
  // point. The horizontal bound keeps the exemption inside the underfoot band.
  if (deliberateFall.get(bot) === block && bot.entity.onGround &&
      Math.abs(p.y - b.y - 1) < 0.6 && Math.hypot(p.x - b.x - 0.5, p.z - b.z - 0.5) < 1.2) return
  if (isOwnSupport(bot, b) || floors.get(bot)?.has(key(b))) {
    throw new MiningError('MINING_PROTECTED_FLOOR', `Do not dig the bot's support or a mine return-path floor at ${b}. Use mine_resources to build an accessible passage.`)
  }
}

/** Break the support under the bot for a planned, survivable free fall.
 * @param {Bot} bot @param {Block} block @param {AbortSignal} signal
 */
async function breakForFall (bot, block, signal) {
  signal.throwIfAborted()
  deliberateFall.set(bot, block)
  try { await bot.dig(block, true, 'raycast'); signal.throwIfAborted() } finally { deliberateFall.delete(bot) }
  deliberateFallAt.set(bot, Date.now())
}

/** When the last planned fall started, so survival can ignore its own damage.
 * @type {WeakMap<Bot, number>} */
const deliberateFallAt = new WeakMap()

/** @param {Bot} bot */
function recentDeliberateFall (bot) {
  const at = deliberateFallAt.get(bot)
  return at != null && Date.now() - at < 8000
}

/** Remove a recorded temporary support with at most one block of descent.
 * Caller must establish placement ownership and check a safe landing block.
 * The exception exists only for this block during this awaited operation.
 * @param {Bot} bot @param {Block} block @param {AbortSignal} signal
 */
async function descendTemporarySupport (bot, block, signal) {
  signal.throwIfAborted()
  temporaryDescent.set(bot, block)
  try { await bot.dig(block, true, 'raycast'); signal.throwIfAborted() } finally { temporaryDescent.delete(bot) }
}

/** Actual collection may break a target; travelling to it may not break a path.
 * A surface soil strip order digs its own standing floor to descend one layer
 * at a time; the allowance applies only while the bot is grounded on it, and
 * the dig itself still goes through the strict temporary-descent gate.
 * @param {Bot} bot @param {Block} block @param {boolean} [allowOwnSupport]
 * @returns {boolean}
 */
function canHarvestBlock (bot, block, allowOwnSupport = false) {
  if (!(allowOwnSupport && bot.entity.onGround && isOwnSupport(bot, block.position))) {
    try { assertFloorSafe(bot, block) } catch { return false }
  }
  const checks = new Movements(bot)
  return checks.safeToBreak(checks.getBlock(block.position, 0, 0, 0))
}

/** @param {Bot} bot */
function installSafety (bot) {
  if (installed.has(bot)) return
  installed.add(bot)
  // Plain travel may clear foliage but never terrain: every non-leaf block id
  // becomes unbreakable, leaves stay the sanctioned digging exception.
  const solidIds = new Set()
  for (const [name, definition] of Object.entries(bot.registry?.blocksByName ?? {})) {
    if (!/(?:^|_)leaves$/.test(name) && !/^leaf_litter$/.test(name) && Number.isInteger(definition?.id)) solidIds.add(definition.id)
  }
  // Forensics: capture who preempts an active pathfinder goal with another
  // goal. Clean stops (goal -> null) are routine walk cleanup and stay silent.
  const setGoalRaw = bot.pathfinder.setGoal.bind(bot.pathfinder)
  bot.pathfinder.setGoal = (goal, reset) => {
    const active = bot.pathfinder.goal
    if (active != null && goal != null && active !== goal) {
      const caller = (new Error('caller').stack ?? '').split(String.fromCharCode(10)).slice(2, 6).join(' | ')
      console.error('[anelf] goal preempted:', active.constructor?.name, '->', goal.constructor?.name, 'by', caller)
    }
    return setGoalRaw(goal, reset)
  }
  const setMovements = bot.pathfinder.setMovements.bind(bot.pathfinder)
  bot.pathfinder.setMovements = movements => {
    if (movements == null) return setMovements(movements)
    if (movements.anelfGatherMode == null) {
      movements.anelfTravelDig = true
      movements.blocksCantBreak = new Set(solidIds)
    }
    return setMovements(restrictMovements(movements))
  }
  if (bot.pathfinder.movements) bot.pathfinder.setMovements(bot.pathfinder.movements)
  // collectblock installs its own movement profile on each collection.
  // setMovements above also constrains profiles supplied later by tools/plugins.
  const dig = bot.dig.bind(bot)
  bot.dig = async (block, forceLook) => {
    assertFloorSafe(bot, block)
    await dig(block, forceLook === 'ignore' ? false : (forceLook ?? true), 'raycast')
  }
}

module.exports = { MiningError, key, equipHarvestTool, restrictMovements, protectFloor, assertFloorSafe, isOwnSupport, canHarvestBlock, installSafety, descendTemporarySupport, breakForFall, recentDeliberateFall }
