/** Isolated vanilla world and real stdio MCP executor for application-level latency measurements. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const { createWriteStream } = require('node:fs')
const { spawn } = require('node:child_process')
const { createRequire } = require('node:module')
const { createServer } = require('node:http')
const { randomUUID } = require('node:crypto')
const { pathToFileURL } = require('node:url')
const path = require('node:path')
const net = require('node:net')
const os = require('node:os')
const { setTimeout: delay } = require('node:timers/promises')

const [payload, directory, java] = process.argv.slice(2).map(value => path.resolve(value))
assert.ok(payload && directory && java)
const deps = createRequire(path.join(payload, 'package.json'))
const load = name => import(pathToFileURL(deps.resolve(name)).href)
const lines = [], events = [], chats = []
let child, ctx, server, probe, http, ready = false, closing = false, failure = null
let tickReport = []
const username = 'AnelfBot', player = 'heiyue'
const token = randomUUID()

async function until (predicate, timeout = 15000) {
  const deadline = Date.now() + timeout
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error('Fixture condition timed out')
    await delay(50)
  }
}

async function command (text) {
  const marker = `__m0_${randomUUID()}__`, start = lines.length
  child.stdin.write(text + '\nsay ' + marker + '\n')
  await until(() => lines.slice(start).some(line => line.includes(marker)))
}

function snapshot () {
  const bot = ctx?.manager.botOrNull()
  return { ready, failure, at: Date.now(), action: ctx?.locks.status(), metrics: ctx?.locks.metrics.snapshot(), tickReport,
    events, chats, inventory: bot?.inventory?.items().map(item => ({ name: item.name, count: item.count })),
    position: bot?.entity?.position, health: bot?.health, food: bot?.food }
}

async function reset (mode) {
  assert.ok(ready && !ctx.locks.action, 'Wait for the actual action to drain before resetting')
  const bot = ctx.manager.requireBot()
  await deps('mineflayer/lib/anelf_inventory.js').restoreCrafting(bot)
  await command('kill @e[type=!minecraft:player]')
  await command('fill -12 64 -12 12 73 12 air')
  await command('fill -12 63 -12 12 63 12 stone')
  await command(`clear ${username}`)
  await command(`tp ${username} 0.5 64 0.5`)
  await command(`tp ${player} 3.5 64 3.5`)
  await command(`effect give ${username} minecraft:instant_health 1 10 true`)
  await command(`effect give ${username} minecraft:saturation 1 10 true`)
  await command('time set day')
  await command('weather clear')
  await command(`give ${username} minecraft:oak_log 3`)
  await delay(400)
  const cursor = lines.length
  await command('tick query')
  tickReport = lines.slice(cursor).filter(line => !line.includes('__m0_'))
  if (mode === 'stop' || mode === 'shortcut') {
    // Real continuous work, started through the same action owner as the MCP follow tool.
    probe.chat('!follow')
    await until(() => Boolean(ctx.locks.action))
  }
  events.length = 0; chats.length = 0
  return snapshot()
}

async function startWorld () {
  await fs.mkdir(directory, { recursive: true })
  const existing = await fs.readdir(directory)
  assert.ok(existing.includes('.anelf-m0-test') || !existing.some(name => ['world', 'server.properties', 'level.dat'].includes(name)),
    'Refusing an existing non-test world')
  await fs.writeFile(path.join(directory, '.anelf-m0-test'), 'Isolated M0 benchmark fixture\n')
  const reserve = net.createServer()
  await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve))
  const port = reserve.address().port
  await new Promise(resolve => reserve.close(resolve))
  await fs.writeFile(path.join(directory, 'eula.txt'), 'eula=true\n')
  await fs.writeFile(path.join(directory, 'server.properties'), [
    'server-ip=127.0.0.1', `server-port=${port}`, 'online-mode=false', 'enforce-secure-profile=false',
    'enable-rcon=false', 'max-players=2', 'view-distance=3', 'simulation-distance=3', 'spawn-protection=0',
    'difficulty=peaceful', 'gamemode=survival', 'level-name=world', 'level-type=minecraft:flat',
    'generate-structures=false', 'pause-when-empty-seconds=-1',
    'generator-settings={"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains"}', '',
  ].join('\n'))
  const log = createWriteStream(path.join(directory, 'server-console.log'))
  child = spawn(java, ['-XX:ActiveProcessorCount=2', '-Xms256M', '-Xmx1G', '-jar', 'server.jar', 'nogui'],
    { cwd: directory, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
  child.on('error', error => { failure = String(error) })
  child.stdin.on('error', error => { failure = String(error) })
  try { os.setPriority(child.pid, os.constants.priority.PRIORITY_BELOW_NORMAL) } catch {}
  for (const stream of [child.stdout, child.stderr]) {
    let tail = ''
    stream.on('data', data => {
      log.write(data); tail += data.toString()
      const parts = tail.split(/\r?\n/); tail = parts.pop(); lines.push(...parts)
    })
  }
  child.once('close', () => log.end())
  await fs.writeFile(path.join(directory, 'java-process.json'), JSON.stringify({ pid: child.pid, parent: process.pid, directory }))
  await until(() => {
    if (failure || child.exitCode !== null) throw new Error(failure || `Server exited: ${child.exitCode}`)
    return lines.some(line => /Done \([\d.]+s\)/.test(line))
  }, 90000)
  await command('fill -16 63 -16 16 63 16 stone')
  await command('setworldspawn 0 64 0')
  await ctx.manager.connect({ host: '127.0.0.1', port, username, auth: 'offline', version: '26.1', viewDistance: 'short' })
  probe = deps('mineflayer').createBot({ host: '127.0.0.1', port, username: player, auth: 'offline', version: '26.1', viewDistance: 'short' })
  probe.on('error', error => { failure = String(error) })
  probe.on('messagestr', text => {
    if (text.startsWith(`<${username}>`)) chats.push({ at: Date.now(), text })
  })
  await until(() => Boolean(probe.entity) && ctx.manager.statusReport().status === 'online')
  ready = true
}

async function finishWorld () {
  ready = false
  try { await ctx?.manager.disconnect('Isolated benchmark finished') } catch {}
  probe?.quit()
  if (child && child.exitCode === null) {
    child.stdin.write('stop\n')
    try { await until(() => child.exitCode !== null, 12000) } catch { child.kill() }
  }
}

async function close () {
  if (closing) return
  closing = true
  await finishWorld()
  http?.close()
  process.exit(0)
}

async function main () {
  const { buildServer } = await import(pathToFileURL(path.join(payload, 'node_modules/awesome-mineflayer-mcp/dist/server.js')).href)
  const { StdioServerTransport } = await load('@modelcontextprotocol/sdk/server/stdio.js')
  ;({ server, ctx } = buildServer())
  ctx.events.listeners.add(event => {
    if (['chat', 'action_progress', 'production_progress'].includes(event.type)) {
      events.push(event)
      if (events.length > 4000) { failure = 'Fixture event buffer overflow'; events.shift() }
    }
  })
  http = createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json')
    if (req.headers.authorization !== `Bearer ${token}`) { res.writeHead(403); res.end('{}'); return }
    try {
      let body = ''
      for await (const chunk of req) { body += chunk; assert.ok(body.length < 4096) }
      const data = body ? JSON.parse(body) : {}
      let result
      if (req.url === '/state' && req.method === 'GET') result = snapshot()
      else if (req.url === '/reset' && req.method === 'POST') result = await reset(data.mode)
      else if (req.url === '/drain' && req.method === 'POST') { await finishWorld(); result = { stopped: child?.exitCode !== null } }
      else if (req.url === '/say' && req.method === 'POST') {
        assert.ok(ready && typeof data.text === 'string' && data.text.length <= 240)
        result = { sentAt: Date.now() }; probe.chat(data.text)
      } else { res.writeHead(404); res.end('{}'); return }
      res.end(JSON.stringify(result))
    } catch (error) { res.writeHead(500); res.end(JSON.stringify({ error: String(error) })) }
  })
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve))
  await fs.writeFile(path.join(directory, 'control.json'), JSON.stringify({ port: http.address().port, token, pid: process.pid }))
  await server.connect(new StdioServerTransport())
  process.stdin.once('end', close)
  process.once('SIGTERM', close); process.once('SIGINT', close)
  await startWorld()
}

main().catch(error => { failure = String(error.stack || error); process.stderr.write(failure + '\n') })
