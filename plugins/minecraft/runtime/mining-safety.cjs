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

/** @param {Movements} movements @returns {Movements} */
function restrictMovements (movements) {
  movements.canDig = false
  movements.allow1by1towers = false
  movements.allowParkour = false
  movements.allowFreeMotion = false
  movements.allowSprinting = false
  // Pathfinder measures solid landings to the supporting block, one below the feet.
  movements.maxDropDown = 2
  movements.infiniteLiquidDropdownDistance = false
  movements.scafoldingBlocks = []
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

/** @param {Bot} bot @param {Block} block */
function assertFloorSafe (bot, block) {
  const p = bot.entity.position
  const b = block.position
  const half = (bot.entity.width || 0.6) / 2
  const supportsBot = b.y < p.y && b.y + 1 >= p.y - 0.1 &&
    b.x < p.x + half && b.x + 1 > p.x - half &&
    b.z < p.z + half && b.z + 1 > p.z - half
  if (supportsBot || floors.get(bot)?.has(key(b))) {
    throw new MiningError('MINING_PROTECTED_FLOOR', `Do not dig the bot's support or a mine return-path floor at ${b}. Use mine_resources to build an accessible passage.`)
  }
}

/** Actual collection may break a target; travelling to it may not break a path.
 * @param {Bot} bot @param {Block} block @returns {boolean}
 */
function canHarvestBlock (bot, block) {
  try { assertFloorSafe(bot, block) } catch { return false }
  const checks = new Movements(bot)
  return checks.safeToBreak(checks.getBlock(block.position, 0, 0, 0))
}

/** @param {Bot} bot */
function installSafety (bot) {
  if (installed.has(bot)) return
  installed.add(bot)
  const setMovements = bot.pathfinder.setMovements.bind(bot.pathfinder)
  bot.pathfinder.setMovements = movements => setMovements(restrictMovements(movements))
  bot.pathfinder.setMovements(bot.pathfinder.movements)
  // collectblock installs its own movement profile on each collection.
  // setMovements above also constrains profiles supplied later by tools/plugins.
  const dig = bot.dig.bind(bot)
  bot.dig = async (block, forceLook) => {
    assertFloorSafe(bot, block)
    await dig(block, forceLook === 'ignore' ? false : (forceLook ?? true), 'raycast')
  }
}

module.exports = { MiningError, key, restrictMovements, protectFloor, assertFloorSafe, canHarvestBlock, installSafety }
