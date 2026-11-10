// Controlled descent policy: fall when survivable, dig only when lethal.
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const { Vec3 } = deps('vec3')
const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)

/** Build a fake bot standing at (0, feetY, 0) over a described column. */
function descentBot ({ feetY, column, health = 20 }) {
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.5, feetY, 0.5), onGround: true, velocity: new Vec3(0, 0, 0) }
  bot.health = health
  bot.blockAt = p => {
    const key = `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`
    const name = column.get(Math.floor(p.y)) ?? 'air'
    // Registry reality: fluids are collision-less, exactly like air.
    const fluid = name === 'lava' || name === 'water'
    return { name, position: new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)),
      boundingBox: fluid || name === 'air' ? 'empty' : 'block' }
  }
  bot.inventory = { items: () => [{ name: 'dirt', count: 64 }] }
  bot.equip = async () => {}
  bot.placeBlock = async (reference, face) => {
    const target = reference.position.plus(face).floored()
    if ((column.get(Math.floor(target.y)) ?? 'air') !== 'air') throw new Error('occupied')
    column.set(Math.floor(target.y), 'dirt')
  }
  bot.dig = async block => {
    if (block.name === 'air') throw new Error('nothing to dig')
    column.set(Math.floor(block.position.y), 'air')
    // Physics: the bot falls to stand on the next solid below.
    let y = Math.floor(bot.entity.position.y) - 1
    while ((column.get(y) ?? 'air') === 'air') y--
    bot.entity.position = new Vec3(0.5, y + 1, 0.5)
  }
  return bot
}

test('a survivable fall breaks the support and takes the drop', async () => {
  const { descend } = await loadInternal()
  // Feet at y=70; open air down to a stone landing at y=63 (7-block fall, 4 damage).
  const column = new Map([[63, 'stone'], [69, 'oak_leaves']])
  const bot = descentBot({ feetY: 70, column })
  const result = await descend(bot, new AbortController().signal, 63)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true)
  assert.equal(result.dug, 1, 'Only the leaf support breaks; the rest is one free fall')
  assert.equal(Math.floor(bot.entity.position.y), 64, 'The bot lands on the stone surface')
})

test('solid ground descends in survivable single-block drops to the target height', async () => {
  const { descend } = await loadInternal()
  // A stone peak: solid column down to y=20; the resources sit at y=26.
  const column = new Map()
  for (let y = 20; y <= 69; y++) column.set(y, 'stone')
  const bot = descentBot({ feetY: 70, column })
  const result = await descend(bot, new AbortController().signal, 26)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true, 'Any survivable drop is taken, even a single block')
  assert.equal(result.dug, 70 - 26 - 2, 'The descent stops two blocks above the target height')
  assert.equal(Math.floor(bot.entity.position.y), 28)
})

test('an open shaft is bridged with carried dirt for a zero-damage dig-down', async () => {
  const { descend } = await loadInternal()
  // A thin support over open air down to stone at y=25: the direct fall kills.
  // The player trick applies: place dirt against the support underside, dig
  // the support, land exactly one block lower — never falling more than one.
  const gap = new Map([[69, 'stone'], [25, 'stone']])
  const bot = descentBot({ feetY: 70, column: gap })
  const result = await descend(bot, new AbortController().signal, 30)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true, 'Bridging digs until the remaining fall turns survivable, then falls')
  assert.ok(result.dug >= 28, 'Lethal stretches are bridged and dug block by block')
  assert.equal(Math.floor(bot.entity.position.y), 26)
  // A survivable-looking drop onto lava never happens.
  const lava = new Map([[69, 'oak_leaves'], [40, 'lava']])
  const bot2 = descentBot({ feetY: 70, column: lava })
  await assert.rejects(descend(bot2, new AbortController().signal), /Landing is lava/)
})

test('a hazard revealed under the first landing refuses the next fall', async () => {
  const { descend } = await loadInternal()
  // Stone at 63 hides lava at 62: the first fall is survivable, and only the
  // rescanned column below the new feet exposes the lava — a stale first
  // landing would break the stone and drop the bot into it.
  const column = new Map([[69, 'oak_leaves'], [63, 'stone'], [62, 'lava']])
  const bot = descentBot({ feetY: 70, column })
  await assert.rejects(descend(bot, new AbortController().signal), /Landing is lava/)
  assert.equal(Math.floor(bot.entity.position.y), 64, 'The bot stays on the stone, never enters the lava')
})

test('an edge stand finds its real support in a neighbouring cell', async () => {
  const { descend } = await loadInternal()
  // The bot stands across the boundary of two cells: air under its floored
  // feet cell (0,70,0), a leaf under the overlapped neighbour (1,69,0). The
  // support scan must find and break that leaf instead of refusing NO_SUPPORT.
  const solid = new Set(['1,69,0', '0,64,0'])
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.9, 70, 0.5), onGround: true, velocity: new Vec3(0, 0, 0) }
  bot.health = 20
  bot.blockAt = p => {
    const name = solid.has(`${p.x},${p.y},${p.z}`) ? 'oak_leaves' : 'air'
    return { name, position: new Vec3(p.x, p.y, p.z), boundingBox: name === 'air' ? 'empty' : 'block' }
  }
  bot.dig = async block => {
    assert.equal(`${block.position.x},${block.position.y},${block.position.z}`, '1,69,0',
      'The real support leaf is the neighbour cell, not the air under the feet cell')
    solid.delete(`${block.position.x},${block.position.y},${block.position.z}`)
    // Physics: the bot falls straight down its own column onto the stone.
    bot.entity.position = new Vec3(0.9, 65, 0.5)
  }
  const result = await descend(bot, new AbortController().signal, 65)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true)
  assert.equal(result.dug, 1)
  assert.equal(Math.floor(bot.entity.position.y), 65)
})

test('staircase escape digs through sand toward the nearest walkable exit', async t => {
  const { findWalkableExit, staircaseEscape } = await load('tools/anelf-descent-tools.mjs')
  // A 3D pocket: the bot sits at y55 in a one-wide slot; a walkable shelf
  // opens three blocks east at y58; sand hangs over the carve path.
  const solid = new Map()
  const put = (x, y, z, name) => solid.set(`${x},${y},${z}`, name)
  const remove = (x, y, z) => solid.delete(`${x},${y},${z}`)
  // Floor under the whole path and the shelf.
  for (let x = -2; x <= 6; x++) for (let z = -2; z <= 2; z++) put(x, 54, z, 'terracotta')
  // Walls around the slot except east.
  for (let y = 55; y <= 61; y++) { put(-1, y, 0, 'terracotta'); put(0, y, -1, 'terracotta'); put(0, y, 1, 'terracotta') }
  // Solid plug on the climb path (steps east) with sand above the headroom.
  for (let x = 1; x <= 3; x++) for (let y = 55; y <= 57; y++) put(x, y, 0, 'terracotta')
  put(2, 60, 0, 'sand'); put(3, 60, 0, 'sand')
  // The open shelf at x>=4, y58: standable with headroom and open neighbours.
  for (let x = 4; x <= 6; x++) put(x, 57, 0, 'terracotta')

  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.5, 55, 0.5), onGround: true, velocity: new Vec3(0, 0, 0) }
  bot.health = 20
  bot.registry = deps('prismarine-registry')('26.1')
  bot.inventory = { items: () => [] }
  bot.blockAt = p => {
    const name = solid.get(`${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`) ?? 'air'
    return { name, boundingBox: name === 'air' ? 'empty' : 'block',
      position: new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)), type: 1 }
  }
  const digs = []
  let sandFell = 0
  bot.dig = async block => {
    const key = `${block.position.x},${block.position.y},${block.position.z}`
    if (block.name === 'sand') sandFell++
    remove(block.position.x, block.position.y, block.position.z)
    digs.push(key)
    // Falling sand: the first dig under a sand cell drops it one level.
    const above = solid.get(`${block.position.x},${block.position.y + 1},${block.position.z}`)
    if (above === 'sand') {
      remove(block.position.x, block.position.y + 1, block.position.z)
      solid.set(`${block.position.x},${block.position.y},${block.position.z}`, 'sand')
    }
  }
  bot.stopDigging = () => {}
  bot.tool = { equipForBlock: async () => {} }
  bot.placeBlock = async () => {}
  bot.setControlState = (state, on) => {
    if (state === 'forward' && on) {
      // Physics: one forward-jump beat moves onto the faced cell.
      const yaw = bot.entity.yaw ?? 0
      const facing = new Vec3(Math.round(-Math.sin(yaw)), 0, Math.round(-Math.cos(yaw)))
      const ahead = bot.entity.position.floored().offset(facing.x, 1, facing.z)
      bot.entity.position = ahead.offset(0.5, 0, 0.5)
    }
  }
  bot.lookAt = async (v) => {
    const delta = v.minus(bot.entity.position.offset(0, 1.62, 0))
    bot.entity.yaw = Math.atan2(-delta.x, -delta.z)
  }

  const exit = findWalkableExit(bot)
  assert.ok(exit, 'A walkable exit is found')
  assert.ok(exit.y > 55, 'The exit stands above the pocket floor')
  const result = await staircaseEscape(bot, new AbortController().signal, exit)
  assert.equal(result.completed, true)
  assert.ok(Math.floor(bot.entity.position.y) >= exit.y - 1, 'The bot walked up to the exit level')
  assert.ok(sandFell > 0 || digs.length > 0, 'The carve actually dug')
})

test('escape and descent placements join the scaffold cleanup ledger', async t => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'anelf-escape-ledger-'))
  const previous = process.env.AWESOME_MINEFLAYER_MCP_HOME
  process.env.AWESOME_MINEFLAYER_MCP_HOME = home
  const { ascend, descend } = await load('tools/anelf-descent-tools.mjs')
  const { appendScaffolds } = await load('tools/anelf-gather-navigation.mjs')
  const crypto = await import('node:crypto')
  try {
    // Ascend: the fake pathfinder places one pillar block through the wrapped
    // bot.placeBlock; the record must land in the ledger file.
    const solid = new Set(['0,63,0'])
    const bot = new EventEmitter()
    bot.entity = { position: new Vec3(0.5, 60, 0.5), onGround: true, velocity: new Vec3(0, 0, 0) }
    bot.health = 20
    bot.registry = deps('prismarine-registry')('26.1')
    bot.inventory = { items: () => [{ name: 'dirt', count: 12, type: bot.registry.itemsByName.dirt.id }] }
    bot.blockAt = p => ({ name: solid.has(`${p.x},${p.y},${p.z}`) ? 'stone' : 'air',
      boundingBox: solid.has(`${p.x},${p.y},${p.z}`) ? 'block' : 'empty', type: 1,
      position: new Vec3(p.x, p.y, p.z) })
    const placed = []
    bot.placeBlock = async (reference, face) => {
      const at = reference.position.plus(face).floored()
      solid.add(`${at.x},${at.y},${at.z}`)
      placed.push(`${at.x},${at.y},${at.z}`)
    }
    bot.pathfinder = {
      setMovements: () => {},
      goto: async () => {
        // Simulate pillaring three blocks under the bot.
        for (const y of [60, 61, 62]) {
          await bot.placeBlock({ position: new Vec3(0, y, 0) }, new Vec3(0, 1, 0))
        }
        bot.entity.position = new Vec3(0.5, 63, 0.5)
      },
      stop: () => {},
    }
    // Rim at y63 ring so the climb target is 63.
    const result = await ascend(bot, new AbortController().signal, 63, undefined, undefined, '["t",1,"B","overworld"]')
    assert.equal(result.completed, true)
    assert.equal(placed.length, 3)
    const key = crypto.createHash('sha256').update('["t",1,"B","overworld"]').digest('hex')
    const ledger = JSON.parse(await fs.readFile(path.join(home, 'scaffolds', `${key}.json`), 'utf8'))
    assert.equal(ledger.scaffolds.length, 3, 'Every pillar block joins the cleanup ledger')

    // Descend bridge: a shaft with one bridging placement records it too.
    const column = new Map([[69, 'oak_leaves'], [25, 'stone']])
    const bot2 = descentBot({ feetY: 70, column })
    bot2.placeBlock = async (reference, face) => {
      const at = reference.position.plus(face).floored()
      column.set(Math.floor(at.y), 'dirt')
    }
    await descend(bot2, new AbortController().signal, 30, '["t",1,"B","overworld"]')
    const ledger2 = JSON.parse(await fs.readFile(path.join(home, 'scaffolds', `${key}.json`), 'utf8'))
    assert.equal(ledger2.scaffolds.length, 3,
      'Descent bridges self-consume on the way down; nothing is left behind to ledger')
  } finally {
    if (previous === undefined) delete process.env.AWESOME_MINEFLAYER_MCP_HOME
    else process.env.AWESOME_MINEFLAYER_MCP_HOME = previous
    await fs.rm(home, { recursive: true, force: true })
  }
})

test('a fluid hazard above the pool floor is never fallen through', async () => {
  const { descend } = await loadInternal()
  // Lava floating over the pool floor stone: the collision-less fluid used to
  // be skipped like air, making the scan land on the stone below it.
  const column = new Map([[69, 'oak_leaves'], [64, 'lava'], [63, 'stone']])
  const bot = descentBot({ feetY: 70, column })
  bot.dig = async () => { throw new Error('REACHED_DIG: lava must stop the descent before any dig') }
  await assert.rejects(descend(bot, new AbortController().signal, 63), /Landing is lava/)
})

test('a fall whose physics lands late still settles before the next step', async () => {
  const { descend } = await loadInternal()
  // The dig promise resolves before the physics tick: a stale onGround read
  // used to end the settle instantly and the loop re-decided at the pre-fall
  // position.
  const column = new Map([[69, 'oak_leaves'], [63, 'stone']])
  const bot = descentBot({ feetY: 70, column })
  bot.dig = async block => {
    column.set(Math.floor(block.position.y), 'air')
    setTimeout(() => {
      let y = Math.floor(bot.entity.position.y) - 1
      while ((column.get(y) ?? 'air') === 'air') y--
      bot.entity.position = new Vec3(0.5, y + 1, 0.5)
    }, 50)
  }
  const result = await descend(bot, new AbortController().signal, 63)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true)
  assert.equal(result.dug, 1)
  assert.equal(Math.floor(bot.entity.position.y), 64)
})

test('the pillar escape builds a placement-only tower profile', async () => {
  const { ascend } = await load('tools/anelf-descent-tools.mjs')
  const { Movements, goals } = deps('mineflayer-pathfinder')
  const bot = new EventEmitter()
  bot.registry = deps('prismarine-registry')('26.1')
  bot.entity = { position: new Vec3(0.5, 60, 0.5) }
  bot.inventory = { items: () => [{ name: 'dirt', count: 12, type: bot.registry.itemsByName.dirt.id }] }
  // A dug pit: open around the feet, ring terrain standing at y=64.
  bot.blockAt = p => ({ boundingBox: p.y === 63 && Math.abs(p.x) >= 2 ? 'block' : 'empty',
    position: new Vec3(p.x, p.y, p.z) })
  const seen = []
  bot.pathfinder = {
    setMovements: movements => seen.push(movements),
    goto: async goal => {
      assert.ok(goal instanceof goals.Goal)
      assert.equal(goal.heuristic({ y: 60 }), 4)
      bot.entity.position = new Vec3(0.5, 64, 0.5)
    },
    stop: () => {},
  }
  const result = await ascend(bot, new AbortController().signal, 64)
  assert.equal(result.completed, true)
  assert.equal(result.from, 60)
  assert.equal(result.to, 64)
  assert.equal(seen.length, 1)
  assert.equal(seen[0].anelfEscape, true)
  assert.equal(seen[0].canDig, true, 'The escape may carve natural terrain')
  assert.equal(seen[0].allow1by1towers, true)
  assert.deepEqual(seen[0].scafoldingBlocks, [bot.registry.itemsByName.dirt.id, bot.registry.itemsByName.cobblestone.id])

  // Flat surroundings rise nothing above the feet: a no-op climb reports
  // failure so the caller can fall back to its cooldown.
  bot.entity.position = new Vec3(0.5, 60, 0.5)
  bot.blockAt = p => ({ boundingBox: p.y === 59 ? 'block' : 'empty', position: new Vec3(p.x, p.y, p.z) })
  bot.pathfinder.goto = async () => { throw new Error('no climb expected') }
  const flat = await ascend(bot, new AbortController().signal)
  assert.equal(flat.completed, false)
  assert.equal(flat.to, 60)

  // No carried material refuses before any movement.
  bot.blockAt = p => ({ boundingBox: p.y === 70 && Math.abs(p.x) >= 2 ? 'block' : 'empty', position: new Vec3(p.x, p.y, p.z) })
  bot.inventory = { items: () => [] }
  await assert.rejects(ascend(bot, new AbortController().signal, 64), /No carried dirt/)
})

async function loadInternal () {
  const { descend } = await load('tools/anelf-descent-tools.mjs')
  return { descend }
}
