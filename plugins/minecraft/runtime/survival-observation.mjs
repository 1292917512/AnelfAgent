// @ts-check
/** Local, read-only survival observations and conservative escape destinations. */
import { Vec3 } from 'vec3'

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-block').Block} Block */
/** @typedef {'drowning'|'burning'|'trapped'|'hurt'|'hostile'|'stuck'} Hazard */
/** @typedef {{health:number|null,oxygen:number|null,position:Vec3|null,headWater:boolean,
 * burning:boolean,trapped:boolean,known:boolean,threat:import('prismarine-entity').Entity|null,threatDistance:number}} Observation */

const hostiles = new Set(['zombie', 'zombie_villager', 'husk', 'drowned', 'skeleton', 'stray', 'bogged',
  'creeper', 'spider', 'cave_spider', 'witch', 'pillager', 'vindicator', 'ravager', 'blaze', 'wither_skeleton',
  'hoglin', 'zoglin', 'silverfish', 'endermite', 'phantom', 'slime', 'magma_cube', 'warden'])
const dangerous = new Set(['lava', 'fire', 'soul_fire', 'campfire', 'soul_campfire', 'magma_block',
  'cactus', 'sweet_berry_bush', 'powder_snow', 'wither_rose'])

/** @param {unknown} value @returns {number|null} */
function finite (value) { return typeof value === 'number' && Number.isFinite(value) ? value : null }
/** @param {Block|null} block */
export function clear (block) { return Boolean(block && block.boundingBox === 'empty' && !dangerous.has(block.name)) }
/** @param {Block|null} block */
export function water (block) { return block?.name === 'water' || block?.name === 'bubble_column' }
/** @param {Block|null} block */
export function solid (block) {
  return Boolean(block && !dangerous.has(block.name) && block.shapes.some(shape =>
    shape[0] === 0 && shape[1] === 0 && shape[2] === 0 && shape[3] === 1 && shape[4] === 1 && shape[5] === 1))
}

/** @param {Bot} bot @returns {Observation} */
export function observeSurvival (bot) {
  const health = finite(bot.health), oxygen = finite(bot.oxygenLevel)
  const raw = bot.entity?.position
  const position = raw && [raw.x, raw.y, raw.z].every(Number.isFinite) ? raw.clone() : null
  const feet = position ? bot.blockAt(position) : null
  const head = position ? bot.blockAt(position.offset(0, (bot.entity.height || 1.8) - 0.2, 0)) : null
  const flagsKey = bot.registry?.entitiesByName?.player?.metadataKeys?.indexOf('shared_flags') ?? -1
  const flags = flagsKey >= 0 ? bot.entity?.metadata?.[flagsKey] : undefined
  const burning = (typeof flags === 'number' && (flags & 1) !== 0) ||
    Boolean(feet && ['lava', 'fire', 'soul_fire'].includes(feet.name))
  let threat = null, threatDistance = Infinity
  if (position) for (const entity of Object.values(bot.entities)) {
    if (!entity.name || !hostiles.has(entity.name) || !entity.position || entity.isValid === false) continue
    const distance = position.distanceTo(entity.position)
    if (distance < threatDistance && distance <= 6) { threat = entity; threatDistance = distance }
  }
  return { health, oxygen, position, headWater: water(head), burning, trapped: Boolean(head && head.boundingBox === 'block'),
    known: health !== null && Boolean(feet && head), threat, threatDistance }
}

/** A clear water column with a known air pocket; never swim upward into an unseen ceiling.
 * @param {Bot} bot @returns {boolean}
 */
export function surfaceVisible (bot) {
  const p = bot.entity.position
  for (let dy = 2; dy <= 8; dy++) {
    const block = bot.blockAt(p.offset(0, dy, 0))
    if (!clear(block)) return false
    if (!water(block)) return clear(bot.blockAt(p.offset(0, dy + 1, 0)))
  }
  return false
}

/** Known standing space with an intact floor, no deep drop or environmental damage.
 * @param {Bot} bot @param {Vec3} p @param {boolean} [shallowWater]
 */
export function safeStanding (bot, p, shallowWater = false) {
  const feet = bot.blockAt(p), head = bot.blockAt(p.offset(0, 1, 0))
  return solid(bot.blockAt(p.offset(0, -1, 0))) && clear(feet) && clear(head) && !water(head) &&
    (shallowWater || !water(feet))
}

/** @param {Bot} bot @param {Vec3} p */
export function occupied (bot, p) {
  return Object.values(bot.entities).some(entity => {
    if (!entity.position || entity.isValid === false || entity.name === 'item') return false
    const { x, y, z } = entity.position, half = (entity.width || 0.6) / 2
    return x + half > p.x && x - half < p.x + 1 && z + half > p.z && z - half < p.z + 1 &&
      y + (entity.height || 1.8) > p.y && y < p.y + 1
  })
}

/** @param {Bot} bot @param {Vec3} away @param {boolean} [burning] @returns {Vec3[]} */
export function escapeCandidates (bot, away, burning = false) {
  const start = bot.entity.position.floored()
  /** @type {Array<{point:Vec3,score:number}>} */
  const candidates = []
  for (const radius of [2, 4]) for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    for (const dy of [0, 1, -1]) {
      const point = start.offset(dx * radius, dy, dz * radius)
      if (!safeStanding(bot, point, burning)) continue
      const distance = point.distanceTo(away)
      if (distance < bot.entity.position.distanceTo(away) + 1) continue
      const shallow = burning && water(bot.blockAt(point))
      candidates.push({ point, score: distance - point.distanceTo(start) * 0.25 + (shallow ? 12 : 0) })
    }
  }
  return candidates.sort((a, b) => b.score - a.score).slice(0, 3).map(candidate => candidate.point)
}
