const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')

const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)

function flatBot (ground) {
  return {
    entity: { position: { x: 0, y: ground + 1, z: 0 } },
    pathfinder: { movements: {}, getPathTo: () => ({ status: 'success', path: [{}] }) },
    blockAt (position) {
      if (position.y === ground) return { name: 'grass_block', boundingBox: 'block' }
      return { name: 'air', boundingBox: 'empty' }
    },
  }
}

test('find_build_site selects a continuous plains footprint and returns the future floor level', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const result = findBuildSite(flatBot(64), { x: 0, y: 65, z: 0 }, 8, 7, 2)
  assert.equal(result.ok, true)
  assert.equal(result.terrain, 'plains_like')
  assert.equal(result.home.y, 65)
  assert.equal(result.fillBlocks, 0)
  assert.equal(result.digBlocks, 0)
})

test('find_build_site rejects a deep hole instead of choosing a cave floor', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const bot = flatBot(64)
  bot.blockAt = position => {
    if (position.x >= -16 && position.x <= 16 && position.z >= -16 && position.z <= 16) {
      return { name: 'air', boundingBox: 'empty' }
    }
    if (position.y === 64) return { name: 'grass_block', boundingBox: 'block' }
    return { name: 'air', boundingBox: 'empty' }
  }
  const result = findBuildSite(bot, { x: 0, y: 65, z: 0 }, 8, 7, 2)
  assert.equal(result.ok, false)
  assert.equal(result.reason, 'NO_SAFE_BUILD_SITE')
})

test('find_build_site rejects non-plains ground even when it is flat', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const bot = flatBot(64)
  bot.blockAt = position => position.y === 64
    ? { name: 'sand', boundingBox: 'block' }
    : { name: 'air', boundingBox: 'empty' }
  const result = findBuildSite(bot, { x: 0, y: 65, z: 0 }, 8, 7, 2)
  assert.equal(result.ok, false)
  assert.equal(result.reason, 'NO_SAFE_BUILD_SITE')
})

test('find_build_site skips the nearest candidate when the house perimeter is unreachable', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const bot = flatBot(64)
  bot.pathfinder = {
    movements: {},
    getPathTo (_movements, goal) {
      return goal.x < -5 || goal.z < -5
        ? { status: 'success', path: [{ x: goal.x, y: goal.y, z: goal.z }] }
        : { status: 'partial', path: [] }
    },
  }
  const result = findBuildSite(bot, { x: 0, y: 65, z: 0 }, 8, 7, 0)
  assert.equal(result.ok, true)
  assert.notDeepEqual({ x: result.home.x, z: result.home.z }, { x: 0, z: 0 })
})

test('prepare_build_site levels shallow high and low columns before construction', async () => {
  const { prepareBuildSite } = await load('tools/anelf-build-site.mjs')
  const blocks = new Map()
  const key = (x, y, z) => `${x},${y},${z}`
  for (let x = 0; x < 7; x++) {
    for (let z = 0; z < 7; z++) {
      const ground = x < 3 ? 65 : 63
      for (let y = 60; y <= ground; y++) blocks.set(key(x, y, z), { name: y === ground ? 'grass_block' : 'dirt', boundingBox: 'block' })
    }
  }
  const air = () => ({ name: 'air', boundingBox: 'empty' })
  const bot = {
    entity: { position: { x: 0, y: 65, z: 0 } },
    inventory: { items: () => [{ name: 'dirt', count: 49, type: 1 }] },
    pathfinder: { goto: async () => {}, stop: () => {} },
    stopDigging: () => {},
    equip: async () => {},
    blockAt (position) {
      const block = blocks.get(key(position.x, position.y, position.z)) ?? air()
      return { ...block, position: { x: position.x, y: position.y, z: position.z } }
    },
    dig: async block => { blocks.delete(key(block.position.x, block.position.y, block.position.z)) },
    placeBlock: async (support, face) => {
      blocks.set(key(support.position.x + face.x, support.position.y + face.y, support.position.z + face.z), { name: 'dirt', boundingBox: 'block' })
    },
  }
  const result = await prepareBuildSite(
    { locks: { begin: () => ({ signal: new AbortController().signal, release: () => {} }) } },
    bot,
    { x: 0, y: 65, z: 0 },
    7,
    2,
  )
  assert.equal(result.verified, true)
  assert.ok(result.dug > 0)
  assert.ok(result.filled > 0)
  for (let x = 0; x < 7; x++) {
    for (let z = 0; z < 7; z++) {
      assert.equal(bot.blockAt({ x, y: 64, z }).boundingBox, 'block')
      assert.equal(bot.blockAt({ x, y: 65, z }).name, 'air')
    }
  }
})

test('build_starter_cabin refuses to start a phase when structural planks are missing', async () => {
  const { buildStarterCabin } = await load('tools/anelf-build-site.mjs')
  const bot = {
    inventory: { items: () => [{ name: 'oak_planks', count: 1, type: 1 }] },
    blockAt: position => position.y === 64
      ? { name: 'dirt', boundingBox: 'block', position }
      : { name: 'air', boundingBox: 'empty', position },
    pathfinder: { goto: async () => {}, stop: () => {} },
    stopDigging: () => {},
  }
  await assert.rejects(
    buildStarterCabin({ locks: { begin: () => ({ signal: new AbortController().signal, release: () => {} }) } }, bot,
      { x: 0, y: 65, z: 0 }, 'walls'),
    { code: 'BUILD_MISSING_MATERIALS' },
  )
})

function constructionWorld () {
  const { createRequire } = require('node:module')
  const { Vec3 } = createRequire(path.join(root, 'package.json'))('vec3')
  const blocks = new Map()
  let stock = 256
  const controller = new AbortController()
  const bot = {
    inventory: { type: 'minecraft:inventory', slots: [], items: () => stock ? [{ name: 'birch_planks', count: stock, type: 2 }] : [] },
    pathfinder: { goto: async () => {}, stop: () => {} }, stopDigging: () => {},
    equip: async () => {},
    blockAt (p) {
      const name = blocks.get(p.toString()) ?? (p.y < 65 ? 'dirt' : 'air')
      return { name, boundingBox: name === 'air' ? 'empty' : 'block', position: new Vec3(p.x, p.y, p.z) }
    },
    placeBlock: async (support, face) => {
      blocks.set(support.position.plus(face).toString(), 'birch_planks')
      stock--
    },
    dig: async block => { blocks.delete(block.position.toString()) },
  }
  const ctx = { locks: { begin: () => ({ signal: controller.signal, release: () => {} }) } }
  return { bot, ctx, blocks, controller, Vec3, setStock: count => { stock = count } }
}

test('home batches preserve the explicit anchor, use inventory, and resume without duplicate placement', async () => {
  const { buildHome } = await load('tools/anelf-build-site.mjs')
  const { bot, ctx, blocks, setStock } = constructionWorld()
  const home = { x: 40, y: 65, z: -20 }
  let last
  for (let run = 0; run < 12; run++) {
    const before = blocks.size
    last = await buildHome(ctx, bot, home, 2, { maxBlocks: 16 })
    assert.deepEqual(last.home, home)
    assert.equal(last.material, 'birch_planks')
    assert.ok(last.placed <= 16)
    assert.equal(blocks.size - before, last.placed)
    if (last.verified) break
  }
  assert.equal(last.verified, true)
  assert.equal(last.nextPhase, 'interior')
  assert.equal(blocks.size, 168)
  setStock(0)
  const repeated = await buildHome(ctx, bot, home, 2, { maxBlocks: 16 })
  assert.equal(repeated.placed, 0)
  assert.equal(repeated.verified, true)
})

test('partial birch foundation needs only missing material and cannot switch species', async () => {
  const { buildStarterCabin } = await load('tools/anelf-build-site.mjs')
  const { bot, ctx, blocks, Vec3, setStock } = constructionWorld()
  const home = { x: 0, y: 65, z: 0 }
  for (let x = 0; x < 7; x++) for (let z = 0; z < 7; z++) blocks.set(new Vec3(x, 65, z).toString(), 'birch_planks')
  blocks.delete(new Vec3(6, 65, 6).toString())
  setStock(1)
  await assert.rejects(buildStarterCabin(ctx, bot, home, 'foundation', { material: 'oak_planks' }), /another plank/)
  const result = await buildStarterCabin(ctx, bot, home, 'foundation')
  assert.equal(result.placed, 1)
  assert.equal(result.verified, true)
})

test('cancellation during equipment selection cannot place a late block', async () => {
  const { buildStarterCabin } = await load('tools/anelf-build-site.mjs')
  const { bot, ctx, blocks, controller } = constructionWorld()
  bot.equip = async () => { controller.abort() }
  await assert.rejects(buildStarterCabin(ctx, bot, { x: 0, y: 65, z: 0 }, 'foundation'), /aborted/)
  assert.equal(blocks.size, 0)
})

test('public build API requires an anchor and rejects unsupported footprints', async () => {
  const { registerBuildSite } = await load('tools/anelf-build-site.mjs')
  const definitions = new Map()
  registerBuildSite(def => definitions.set(def.name, def))
  assert.equal(definitions.get('build_starter_home').inputSchema.home.safeParse(undefined).success, false)
  assert.equal(definitions.get('find_build_site').inputSchema.footprint.safeParse(5).success, false)
})

test('site selection fails when reachability cannot be checked', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const bot = flatBot(64)
  delete bot.pathfinder
  assert.equal(findBuildSite(bot, { x: 0, y: 65, z: 0 }, 8).ok, false)
})
