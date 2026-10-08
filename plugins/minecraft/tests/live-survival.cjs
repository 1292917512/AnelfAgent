// Opt-in survival/production acceptance against an isolated vanilla server.
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const { randomUUID } = require('node:crypto')
const fs = require('node:fs/promises')
const { createWriteStream } = require('node:fs')
const { createRequire } = require('node:module')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { setTimeout: delay } = require('node:timers/promises')
const { pathToFileURL } = require('node:url')

const args = process.argv.slice(2)
const option = name => { const i = args.indexOf(name); return i < 0 ? undefined : args[i + 1] }
const java = option('--java')
const directory = option('--server-dir')
if (!java || !directory) throw new Error('Required: --java <java.exe> --server-dir <isolated directory with server.jar>')
const serverDir = path.resolve(directory)
const payload = path.resolve(option('--payload') || 'workspace/plugins/minecraft-companion')
const deps = createRequire(path.join(payload, 'package.json'))
const load = name => import(pathToFileURL(deps.resolve(name)).href)
const username = 'M2TestBot'
const producer = randomUUID()
const lines = [], events = [], results = [], samples = [], routes = [], packets = []
let child, client, server, ctx, epoch = 0, current = 'startup', bot, sampling
let serverOwned = false, serverClosed = false, serverError

async function waitFor (description, predicate, timeout = 12000) {
  const end = Date.now() + timeout
  while (Date.now() < end) { if (await predicate()) return; await delay(50) }
  throw new Error(`Timed out: ${description}`)
}

function snapshot () {
  const p = bot?.entity?.position
  return { at: Date.now(), test: current, position: p ? { x: p.x, y: p.y, z: p.z } : null,
    health: bot?.health, oxygen: bot?.oxygenLevel, food: bot?.food,
    controls: bot ? { ...bot.controlState } : {}, survival: ctx?.locks.survival.status(), action: ctx?.locks.status(),
    inventory: current.startsWith('production_') && bot?.inventory ? {
      items: bot.inventory.items().map(item => ({ name: item.name, count: item.count, slot: item.slot })),
      window: bot.currentWindow?.type ?? bot.inventory.type,
      cursor: (bot.currentWindow ?? bot.inventory).selectedItem,
      grid: (bot.currentWindow ?? bot.inventory).slots.slice(0, bot.currentWindow ? 10 : 5),
    } : undefined }
}

function metadata (requestId = randomUUID()) {
  return { producer, epoch, floor: epoch, requestId, actor: 'acceptance',
    scope: 'group_minecraft:survival-test', delegationId: '', worldId: 'survival-test' }
}

async function call (name, parameters = {}, origin) {
  if (['cancel_task', 'pause_action', 'disconnect_bot'].includes(name)) epoch++
  const response = await client.callTool({ name, arguments: parameters, _meta: { 'anelf/action': origin || metadata() } })
  if (response.isError) throw new Error(`${name}: ${JSON.stringify(response.content)}`)
  return response.structuredContent ?? JSON.parse(response.content[0].text)
}

async function command (text) {
  const marker = `__m2_ack_${randomUUID()}__`, cursor = lines.length
  child.stdin.write(text + '\nsay ' + marker + '\n')
  await waitFor(`server command ${text}`, () => lines.slice(cursor).some(line => line.includes(marker)), 10000)
}

async function startServer () {
  await fs.mkdir(serverDir, { recursive: true })
  const marker = path.join(serverDir, '.anelf-survival-test')
  const existing = await fs.readdir(serverDir)
  assert.ok(existing.includes('.anelf-survival-test') || !existing.some(name => ['world', 'server.properties', 'level.dat'].includes(name)),
    'Refusing an existing world or server without the test marker')
  await fs.writeFile(marker, 'Isolated Minecraft survival acceptance environment\n')
  serverOwned = true
  const reservation = net.createServer()
  await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve))
  const port = reservation.address().port
  await new Promise(resolve => reservation.close(resolve))
  await fs.writeFile(path.join(serverDir, 'eula.txt'), 'eula=true\n')
  await fs.writeFile(path.join(serverDir, 'server.properties'), [
    'server-ip=127.0.0.1', `server-port=${port}`, 'online-mode=false', 'enforce-secure-profile=false',
    'enable-rcon=false', 'enable-query=false', 'view-distance=3', 'simulation-distance=3', 'max-players=1',
    'spawn-protection=0', 'difficulty=peaceful', 'gamemode=survival', 'level-name=world', 'level-type=minecraft:flat',
    'generate-structures=false', 'max-tick-time=60000', 'pause-when-empty-seconds=-1',
    'generator-settings={"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains"}',
    'motd=Anelf isolated acceptance', '',
  ].join('\n'))
  const log = createWriteStream(path.join(serverDir, 'server-console.log'))
  child = spawn(java, ['-XX:ActiveProcessorCount=2', '-Xms256M', '-Xmx1G', '-jar', 'server.jar', 'nogui'],
    { cwd: serverDir, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
  child.once('error', error => { serverError = error })
  child.stdin.on('error', error => { serverError = error })
  try { os.setPriority(child.pid, os.constants.priority.PRIORITY_BELOW_NORMAL) } catch {}
  for (const stream of [child.stdout, child.stderr]) {
    let partial = ''
    stream.on('data', data => {
      log.write(data)
      partial += data.toString()
      const complete = partial.split(/\r?\n/)
      partial = complete.pop()
      lines.push(...complete)
    })
  }
  child.once('close', () => { serverClosed = true; log.end() })
  await waitFor('vanilla server ready', () => {
    if (serverError) throw serverError
    if (child.exitCode !== null) throw new Error(`Server exited ${child.exitCode}: ${lines.slice(-8).join('\n')}`)
    return lines.some(line => /Done \([\d.]+s\)/.test(line))
  }, 90000)
  await command('fill -16 63 -16 16 63 16 stone')
  await command('setworldspawn 0 64 0')
  console.log(JSON.stringify({ ready: true, server: 'vanilla 26.1', host: '127.0.0.1', port, pid: child.pid }))
  return port
}

async function prepare () {
  await call('configure_survival', { enabled: false, intervalMs: 250 })
  await call('cancel_task')
  await waitFor('previous action drained', () => !ctx.locks.action)
  const window = bot.currentWindow ?? bot.inventory
  if (window.selectedItem || window.slots.slice(0, bot.currentWindow ? 10 : 5).some(Boolean)) {
    await deps('mineflayer/lib/anelf_inventory.js').restoreCrafting(bot)
  }
  await command('difficulty peaceful')
  await command('kill @e[type=!minecraft:player]')
  await command('fill -12 64 -12 12 73 12 air')
  await command('fill -12 63 -12 12 63 12 stone')
  await command('time set day')
  await command('weather clear')
  await command(`clear ${username}`)
  await command(`effect clear ${username}`)
  await command('setblock 0 64 0 water')
  await command(`tp ${username} 0.5 64 0.5`)
  await delay(500)
  await command('setblock 0 64 0 air')
  await command(`effect give ${username} minecraft:instant_health 1 10 true`)
  await command(`effect give ${username} minecraft:saturation 1 10 true`)
  await waitFor('healthy stationary spawn', () => bot.health === 20 && Math.abs(bot.entity.position.y - 64) < 0.2)
  await call('look_at', { x: 2, y: 65, z: 0 })
  await call('configure_survival', { enabled: true, intervalMs: 250 })
  await delay(3000)
}

async function scenario (name, work) {
  current = name
  const start = events.length, at = Date.now()
  try {
    await prepare()
    await work(start)
    results.push({ name, passed: true, elapsedMs: Date.now() - at, final: snapshot() })
  } catch (error) {
    results.push({ name, passed: false, error: String(error.stack || error), final: snapshot(), events: events.slice(start) })
  }
  console.log(JSON.stringify({ ...results.at(-1), final: undefined, events: undefined }))
  await fs.writeFile(path.join(serverDir, 'results.json'), JSON.stringify({ results, events, samples, routes, packets }, null, 2))
}

async function main () {
  const port = await startServer()
  const { InMemoryTransport } = await load('@modelcontextprotocol/sdk/inMemory.js')
  const { Client } = await load('@modelcontextprotocol/sdk/client/index.js')
  const { buildServer } = await import(pathToFileURL(path.join(payload, 'node_modules/awesome-mineflayer-mcp/dist/server.js')).href)
  ;({ server, ctx } = buildServer())
  client = new Client({ name: 'survival-acceptance', version: '1' })
  const [local, remote] = InMemoryTransport.createLinkedPair()
  await server.connect(remote); await client.connect(local)
  await call('connect_bot', { host: '127.0.0.1', port, username, auth: 'offline', version: '26.1', viewDistance: 'short' })
  bot = ctx.manager.requireBot()
  if (args.includes('--trace-inventory')) {
    const relevant = new Set(['window_click', 'window_items', 'set_slot', 'set_player_inventory', 'set_cursor_item', 'close_window', 'open_window', 'block_place', 'held_item_slot'])
    const record = (direction, name, data) => {
      if (relevant.has(name) && packets.length < 10000) packets.push({ at: Date.now(), test: current, direction, name, data: structuredClone(data) })
    }
    const write = bot._client.write.bind(bot._client)
    bot._client.write = (name, data) => { record('out', name, data); return write(name, data) }
    bot._client.on('packet', (data, metadata) => record('in', metadata.name, data))
  }
  const search = bot.pathfinder.getPathTo.bind(bot.pathfinder)
  bot.pathfinder.getPathTo = (movements, goal, timeout) => {
    const found = search(movements, goal, timeout)
    routes.push({ test: current, start: bot.entity.position.clone(), goal, timeout,
      status: found.status, path: found.path.map(step => ({ x: step.x, y: step.y, z: step.z, toBreak: step.toBreak, toPlace: step.toPlace })) })
    return found
  }
  const push = ctx.locks.events.push.bind(ctx.locks.events)
  ctx.locks.events.push = (type, data) => { if (['survival_progress', 'action_progress', 'production_progress'].includes(type)) events.push({ at: Date.now(), test: current, type, data }); return push(type, data) }
  sampling = setInterval(() => samples.push(snapshot()), 100)
  const has = (start, kind, phase) => events.slice(start).some(event => event.type === 'survival_progress' && event.data.kind === kind && event.data.phase === phase)
  const selected = option('--case')
  const suite = option('--suite') || 'survival'
  assert.ok(['survival', 'production', 'all'].includes(suite), `Unknown suite: ${suite}`)
  const test = (name, work) => (!selected || selected === name) &&
    (suite === 'all' || name.startsWith('production_') === (suite === 'production')) ? scenario(name, work) : Promise.resolve()

  await test('water_surface_and_remain_safe', async start => {
    await call('configure_survival', { enabled: false, intervalMs: 250 })
    await command('fill -3 59 -3 3 63 3 stone')
    await command('fill -2 60 -2 2 63 2 water')
    await command(`tp ${username} 0.5 60 0.5`)
    await delay(250)
    await call('configure_survival', { enabled: true, intervalMs: 250 })
    await waitFor('local surfacing starts', () => has(start, 'drowning', 'started'))
    await waitFor('head reaches air', () => !ctx.locks.survival.observation?.headWater, 9000)
    await delay(7000)
    assert.equal(ctx.locks.survival.observation?.headWater, false, 'Must remain safe instead of sinking after two surfacing attempts')
    assert.ok(bot.entity.position.y >= 64 && bot.entity.onGround, 'Recovery must reach a verified standing exit')
    assert.ok(has(start, 'drowning', 'moved'), 'Recovery must confirm its exit instead of merely stopping at the bank edge')
    assert.ok(bot.health > 0)
  })
  await test('stop_during_shore_swim_releases_controls', async start => {
    await call('configure_survival', { enabled: false, intervalMs: 250 })
    await command('fill -5 59 -5 5 63 5 stone')
    await command('fill -4 60 -4 4 63 4 water')
    await command(`tp ${username} 0.5 60 0.5`)
    await delay(250)
    await call('configure_survival', { enabled: true, intervalMs: 250 })
    await waitFor('shore swimming active', () => has(start, 'drowning', 'started') && bot.controlState.forward)
    await call('cancel_task')
    await waitFor('actual survival cleanup', () => !ctx.locks.action && !ctx.locks.survival.pending)
    await delay(1500)
    assert.equal(bot.controlState.forward, false)
    assert.equal(bot.controlState.jump, false)
    assert.equal(ctx.locks.autonomousEnabled, false)
  })
  await test('manual_stop_holds_underwater', async start => {
    await call('cancel_task')
    const p = bot.entity.position.clone()
    await command('fill -2 64 -2 2 67 2 water')
    await waitFor('held danger fact', () => has(start, 'drowning', 'held'))
    await delay(2000)
    assert.equal(bot.controlState.jump, false)
    assert.ok(Math.abs(bot.entity.position.x - p.x) < 0.15 && Math.abs(bot.entity.position.z - p.z) < 0.15)
    assert.equal(ctx.locks.autonomousEnabled, false)
  })
  await test('ceiling_blocks_blind_ascent', async start => {
    await call('configure_survival', { enabled: false, intervalMs: 250 })
    await command('fill -2 64 -2 2 66 2 water')
    await command('fill -2 67 -2 2 67 2 stone')
    await call('configure_survival', { enabled: true, intervalMs: 250 })
    await waitFor('blocked surfacing', () => has(start, 'drowning', 'blocked'))
    assert.equal(bot.controlState.jump, false)
    assert.ok(bot.entity.position.y < 65)
  })
  await test('fire_moves_to_safe_ground', async start => {
    await command('setblock 0 63 0 netherrack')
    await command('setblock 0 64 0 fire')
    await waitFor('fire rescue starts', () => has(start, 'burning', 'started'))
    await waitFor('out of fire', () => bot.entity.position.distanceTo({ x: 0.5, y: 64, z: 0.5 }) > 2)
    await delay(9000)
    assert.ok(bot.health > 0)
    assert.equal(ctx.locks.survival.observation?.burning, false)
  })
  await test('hurt_preempts_and_rejects_old_request', async start => {
    const old = metadata('old-work')
    await call('set_goal', { goalType: 'near', x: 10, y: 64, z: 0, range: 1, dynamic: true }, old)
    await command(`damage ${username} 2 minecraft:generic`)
    await waitFor('hurt rescue', () => has(start, 'hurt', 'started'))
    await waitFor('rescue completes', () => !ctx.locks.survival.pending)
    await assert.rejects(call('look_at', { x: 1, y: 65, z: 0 }, old), /CANCELLED|interrupted/)
    assert.equal(old.epoch, epoch, 'Rejection must come from local revocation, not a later host stop')
    await call('look_at', { x: 2, y: 65, z: 0 })
  })
  await test('zombie_approach_triggers_escape', async start => {
    await command('time set midnight')
    await command('difficulty easy')
    await command('summon minecraft:zombie 3.5 64 0.5 {Tags:["m2_hazard"],PersistenceRequired:1b}')
    await waitFor('hostile or damage rescue', () => has(start, 'hostile', 'started') || has(start, 'hurt', 'started'))
    await waitFor('bot escapes initial position', () => bot.entity.position.distanceTo({ x: 0.5, y: 64, z: 0.5 }) > 2)
    assert.ok(bot.health > 0)
  })
  await test('three_zombies_from_one_side_allow_bounded_escape', async start => {
    await command('time set midnight')
    await command('difficulty easy')
    for (const z of [-1.5, 0.5, 2.5]) await command(`summon minecraft:zombie 4.5 64 ${z} {Tags:["m2_hazard"],PersistenceRequired:1b}`)
    await waitFor('group approach response', () => has(start, 'hostile', 'started') || has(start, 'hurt', 'started'))
    await delay(6000)
    assert.ok(bot.health > 0)
    assert.ok(bot.entity.position.x < 0.5, 'Escape should move away from the approaching group')
  })
  await test('death_respawn_keeps_work_stopped', async start => {
    const old = metadata('pre-death-work')
    await call('set_goal', { goalType: 'near', x: 10, y: 64, z: 0, range: 1, dynamic: true }, old)
    await command(`kill ${username}`)
    await waitFor('server death and respawn', () => has(start, 'death', 'dead') && has(start, 'death', 'respawned'), 15000)
    await delay(1500)
    assert.equal(ctx.locks.autonomousEnabled, false)
    assert.equal(ctx.locks.action, null)
    await assert.rejects(call('look_at', { x: 1, y: 65, z: 0 }, old), /CANCELLED|interrupted/)
  })
  await require('./live-production.cjs')({ test, call, command, waitFor, bot, ctx, username, metadata })
  assert.ok(results.length, `Unknown test case: ${selected}`)
}

main().catch(error => { console.error(error); process.exitCode = 1 }).finally(async () => {
  clearInterval(sampling)
  const clean = async work => { try { await work() } catch (error) { console.error(error); process.exitCode = 1 } }
  if (client) {
    await clean(() => call('cancel_task'))
    await clean(() => call('disconnect_bot', { reason: 'Acceptance finished' }))
    await clean(() => client.close())
  }
  if (server) await clean(() => server.close())
  if (ctx) { await clean(() => ctx.manager.shutdown()); await clean(() => ctx.locks.metrics.close()) }
  if (child?.pid && !serverClosed) {
    if (child.stdin.writable) child.stdin.write('stop\n')
    try { await waitFor('test server shutdown', () => serverClosed, 20000) } catch { child.kill() }
  }
  if (serverOwned) {
    await fs.writeFile(path.join(serverDir, 'results.json'), JSON.stringify({ results, events, samples, routes, packets }, null, 2))
  }
  if (results.some(result => !result.passed)) process.exitCode = 1
  console.log(JSON.stringify({ passed: results.filter(result => result.passed).length, failed: results.filter(result => !result.passed).length, output: path.join(serverDir, 'results.json') }))
})
