// @ts-check
/** Model Experience: failed workbench placement includes locally checked alternative coordinates.
 * Token effect: at most two small candidates, only on placement failure.
 * Cache effect: live block facts stay in tool results; no new tools or prompt prefix changes.
 */
/** @typedef {import('vec3').Vec3} Vec3 */
/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-entity').Entity} Entity */
/** @param {Vec3} position @param {Entity} entity */
function overlaps (position, entity) {
  const p = entity.position
  const half = entity.width / 2
  return position.x < p.x + half && position.x + 1 > p.x - half &&
    position.y < p.y + entity.height && position.y + 1 > p.y &&
    position.z < p.z + half && position.z + 1 > p.z - half
}

/** Read only: no movement, equipment changes, inventory clicks or placement retries.
 * @param {Bot} bot
 */
export function workbenchPlacementHints (bot) {
  const entity = bot.entity
  if (!entity?.position || !(entity.width > 0 && entity.height > 0)) return []
  const base = entity.position.floored()
  const head = entity.position.offset(0, entity.height, 0)
  const bodies = [entity, ...Object.values(bot.entities).filter(e => e.type === 'player' || e.type === 'mob')]
  const cube = [0, 0, 0, 1, 1, 1]
  const air = new Set(['air', 'cave_air', 'void_air'])
  const candidates = []
  for (let dx = -3; dx <= 3; dx++) {
    for (let dz = -3; dz <= 3; dz++) {
      for (let dy = -1; dy <= 1; dy++) {
        const destination = base.offset(dx, dy, dz)
        if (bodies.some(body => overlaps(destination, body))) continue
        const reference = bot.blockAt(destination.offset(0, -1, 0))
        const target = bot.blockAt(destination)
        if (!reference || !target || !air.has(target.name)) continue
        if (reference.shapes.length !== 1 || !reference.shapes[0].every((n, i) => n === cube[i]) || reference.shapes[0].length !== 6) continue
        const face = reference.position.offset(0.5, 1, 0.5)
        if (head.distanceTo(face) > 4) continue
        candidates.push({ reference, distance: entity.position.distanceTo(destination.offset(0.5, 0, 0.5)) })
      }
    }
  }
  candidates.sort((a, b) => a.distance - b.distance)
  const hints = []
  for (const { reference } of candidates) {
    if (!bot.canSeeBlock(reference)) continue
    hints.push({ referenceX: reference.position.x, referenceY: reference.position.y, referenceZ: reference.position.z,
      faceVector: { x: 0, y: 1, z: 0 }, itemName: 'crafting_table' })
    if (hints.length === 2) break
  }
  return hints
}
