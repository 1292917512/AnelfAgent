#!/usr/bin/env node
// @ts-check
/**
 * Headless Minecraft player used for end-to-end chat ingress tests.
 *
 * Model Experience: this harness does not change model input, token usage, or
 * prompt cache layers; it only produces real Minecraft chat events.
 */
const { createRequire } = require('node:module')
const path = require('node:path')
const { setTimeout: delay } = require('node:timers/promises')

const root = path.resolve(__dirname, '..')
const payload = path.resolve(option('--payload', path.join(process.cwd(), 'workspace/plugins/minecraft-companion')))
const deps = createRequire(path.join(payload, 'package.json'))
const mineflayer = deps('mineflayer')

function option (name, fallback = undefined) {
  const index = process.argv.indexOf(name)
  if (index < 0) return fallback
  const value = process.argv[index + 1]
  if (!value || value.startsWith('--')) throw new Error(`${name} requires a value`)
  return value
}

function numberOption (name, fallback) {
  const raw = option(name)
  if (raw === undefined) return fallback
  const value = Number(raw)
  if (!Number.isFinite(value) || value < 0) throw new Error(`${name} must be a non-negative number`)
  return value
}

function usage () {
  console.error('Usage: node tests/live-chat-probe.cjs --username NAME --message TEXT [options]')
  console.error('Options: --host HOST --port PORT --auth offline|microsoft --version VERSION --delay MS --role ROLE')
}

function emit (event, data = {}) {
  process.stdout.write(`${JSON.stringify({ event, at: new Date().toISOString(), epoch_ms: Date.now(), ...data })}\n`)
}

async function main () {
  const username = option('--username')
  const message = option('--message')
  if (!username || !message) {
    usage()
    throw new Error('--username and --message are required')
  }
  const host = option('--host', '127.0.0.1')
  const port = Number(option('--port', '25565'))
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('--port must be 1..65535')
  const auth = option('--auth', 'offline')
  if (!['offline', 'microsoft'].includes(auth)) throw new Error('--auth must be offline or microsoft')
  const version = option('--version', '26.1')
  const waitMs = numberOption('--delay', 1200)
  const role = option('--role', 'test-player')
  const bot = mineflayer.createBot({ host, port, username, auth, version, viewDistance: 'short' })
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    bot.quit('chat probe finished')
  }
  const onEnd = reason => emit('disconnected', { reason: String(reason ?? '') })
  bot.once('end', onEnd)
  bot.once('error', error => emit('error', { message: String(error?.stack || error) }))
  bot.on('messagestr', (text, position) => emit('server_message', { text, position: position ?? null }))
  await new Promise((resolve, reject) => {
    bot.once('spawn', resolve)
    bot.once('error', reject)
  })
  emit('connected', { host, port, username, role })
  await delay(waitMs)
  const sentAt = Date.now()
  bot.chat(message)
  emit('chat_sent', { host, port, username, role, message, sent_at_epoch_ms: sentAt })
  await delay(numberOption('--listen', 2500))
  close()
  await delay(100)
}

main().catch(error => {
  emit('fatal', { message: String(error?.stack || error) })
  process.exitCode = 1
})
