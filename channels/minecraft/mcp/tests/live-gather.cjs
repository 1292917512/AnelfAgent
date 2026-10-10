// Real block removal, inventory receipt and return are tested in the isolated vanilla world.
const assert = require('node:assert/strict')
const { setTimeout: delay } = require('node:timers/promises')

module.exports = async function gatherCases ({ test, call, command, waitFor, bot, ctx, username, metadata, onReconnect }) {
  const count = name => bot.inventory.items().filter(item => item.name === name).reduce((n, item) => n + item.count, 0)
  const position = () => bot.entity.position.floored().toString()
  const defaults = { x: 4, y: 64, z: 0, radius: 2, block: 'stone', count: 2 }
  const block = (x, y = 64, z = 0) => bot.blockAt(bot.entity.position.clone().set(x, y, z))
  async function setup (name = 'stone') {
    await call('configure_survival', { enabled: false, intervalMs: 250 })
    await command(`setblock 4 64 0 ${name}`); await command(`setblock 5 64 0 ${name}`)
    await waitFor('gather targets loaded', () => block(4)?.name === name && block(5)?.name === name)
  }
  async function give (name, amount = 1) {
    const before = count(name)
    await command(`give ${username} ${name} ${amount}`)
    await waitFor('inventory grant', () => count(name) === before + amount)
  }
  async function finish () {
    let state
    await waitFor('gather terminal', async () => { state = await call('gathering_status'); return !state.active }, 65000)
    assert.equal(ctx.locks.action, null)
    return state
  }
  async function run (args = {}) { await call('gather_resources', { ...defaults, ...args }); return finish() }

  await test('gather_stone_new_receipts_and_return', async () => {
    await setup(); await give('wooden_pickaxe'); await give('cobblestone', 7)
    const entry = position(), state = await run()
    assert.equal(state.phase, 'completed', state.reason + state.returnReason)
    assert.equal(state.dug, 2); assert.equal(state.gained, 2); assert.equal(state.remaining, 0)
    assert.equal(state.before, 7); assert.equal(state.available, 9)
    assert.equal(state.returned, true); assert.equal(position(), entry)
    assert.equal(block(0, 63)?.name, 'stone'); assert.equal(block(4, 63)?.name, 'stone')
  })
  await test('gather_logs_by_hand', async () => {
    await setup('oak_log')
    const state = await run({ block: 'oak_log' })
    assert.equal(state.phase, 'completed', state.reason); assert.equal(state.gained, 2); assert.equal(state.returned, true)
  })
  await test('gather_missing_tool_does_not_break_stone', async () => {
    await setup()
    const state = await run()
    assert.equal(state.phase, 'blocked'); assert.equal(state.dug, 0); assert.equal(state.gained, 0)
    assert.equal(block(4).name, 'stone'); assert.equal(state.returned, true)
  })
  await test('gather_never_digs_floor_or_descends', async () => {
    await setup(); await give('wooden_pickaxe')
    await command('fill 4 64 0 5 64 0 air')
    await waitFor('only floor targets remain', () => block(4).name === 'air')
    const entry = position(), state = await run({ x: 0, y: 63, radius: 2 })
    assert.equal(state.phase, 'blocked'); assert.equal(state.dug, 0)
    assert.equal(block(0, 63).name, 'stone'); assert.equal(position(), entry)
  })
  await test('gather_no_drops_cannot_claim_receipt', async () => {
    await setup(); await give('wooden_pickaxe')
    await command('gamerule minecraft:block_drops false')
    try {
      const state = await run()
      assert.equal(state.phase, 'blocked'); assert.match(state.reason, /GATHER_NO_PICKUP/)
      assert.equal(state.dug, 1); assert.equal(state.gained, 0); assert.equal(state.remaining, 2)
      assert.equal(state.returned, true); assert.equal(block(5).name, 'stone')
    } finally { await command('gamerule minecraft:block_drops true') }
  })
  await test('gather_stop_during_dig_never_returns_or_restarts', async () => {
    await setup('oak_log')
    const old = metadata('gather-stop')
    await call('gather_resources', { ...defaults, block: 'oak_log' }, old)
    await waitFor('dig active', () => bot.targetDigBlock)
    await call('cancel_task')
    const state = await finish(), stopped = position()
    assert.equal(state.phase, 'cancelled'); assert.equal(state.returned, false)
    await delay(500); assert.equal(position(), stopped)
    await assert.rejects(call('gather_resources', { ...defaults }, old), /CANCELLED/)
  })
  await test('gather_chest_tool_then_collect_return_and_deliver', async () => {
    await setup(); await give('cobblestone', 7)
    await command('setblock 2 64 0 chest')
    await command('item replace block 2 64 0 container.0 with wooden_pickaxe 1')
    const entry = position(), state = await run({ chest: { x: 2, y: 64, z: 0 }, withdraw: [{ item: 'wooden_pickaxe', count: 1 }], deposit: true })
    assert.equal(state.phase, 'completed', state.reason); assert.equal(state.gained, 2); assert.equal(state.deposited, 2)
    assert.equal(count('cobblestone'), 7); assert.equal(count('wooden_pickaxe'), 1)
    assert.equal(state.returned, true); assert.equal(position(), entry)
    const win = await bot.openContainer(block(2)); await bot._syncWindow(win)
    assert.equal(win.slots.slice(0, win.inventoryStart).filter(Boolean).reduce((n, item) => n + (item.name === 'cobblestone' ? item.count : 0), 0), 2)
    await bot.closeWindow(win)
  })
  await test('gather_chest_shortage_stops_before_excavation', async () => {
    await setup(); await command('setblock 2 64 0 chest')
    const state = await run({ chest: { x: 2, y: 64, z: 0 }, withdraw: [{ item: 'wooden_pickaxe', count: 1 }] })
    assert.equal(state.phase, 'blocked'); assert.equal(state.dug, 0); assert.equal(block(4).name, 'stone')
    assert.equal(state.inventoryClean, true)
  })
  await test('gather_full_chest_retains_confirmed_yield', async () => {
    await setup(); await give('wooden_pickaxe'); await command('setblock 2 64 0 chest')
    const items = Array.from({ length: 27 }, (_, Slot) => ({ Slot, id: 'minecraft:dirt', count: 64 }))
    await command(`data merge block 2 64 0 {Items:${JSON.stringify(items)}}`)
    const state = await run({ chest: { x: 2, y: 64, z: 0 }, deposit: true })
    assert.equal(state.phase, 'blocked'); assert.equal(state.gained, 2); assert.equal(state.deposited, 0)
    assert.equal(state.returned, true); assert.equal(count('cobblestone'), 2); assert.equal(state.inventoryClean, true)
  })
  await test('gather_blocked_return_does_not_dig_an_exit', async () => {
    await setup(); await give('wooden_pickaxe')
    await call('gather_resources', defaults)
    await waitFor('away from entry', () => bot.entity.position.x > 2.5)
    await command('setblock 0 64 0 bedrock')
    const state = await finish()
    assert.equal(state.phase, 'blocked'); assert.equal(state.returned, false)
    assert.ok(state.returnReason); assert.equal(block(0).name, 'bedrock')
  })
  await test('gather_pre_supplied_resources_do_not_satisfy_new_target', async () => {
    await setup(); await give('wooden_pickaxe'); await command('setblock 2 64 0 chest')
    await command('item replace block 2 64 0 container.0 with cobblestone 8')
    const state = await run({ chest: { x: 2, y: 64, z: 0 }, withdraw: [{ item: 'cobblestone', count: 8 }], deposit: true })
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.before, 8); assert.equal(state.dug, 2); assert.equal(state.gained, 2)
    assert.equal(state.deposited, 2); assert.equal(count('cobblestone'), 8)
  })
  await test('gather_full_inventory_stops_before_digging', async () => {
    await setup(); await give('wooden_pickaxe'); await give('dirt', 2240)
    const state = await run()
    assert.equal(state.phase, 'blocked'); assert.match(state.reason, /GATHER_FULL/)
    assert.equal(state.dug, 0); assert.equal(block(4).name, 'stone')
  })
  await test('gather_hurt_preempts_without_resuming', async () => {
    await setup('oak_log'); await call('configure_survival', { enabled: true, intervalMs: 250 })
    const old = metadata('gather-hurt')
    await call('gather_resources', { ...defaults, block: 'oak_log' }, old)
    await waitFor('gather digging before damage', () => bot.targetDigBlock)
    await command(`damage ${username} 2 minecraft:generic`)
    let state
    await waitFor('gather interrupted', async () => { state = await call('gathering_status'); return !state.active })
    assert.equal(state.phase, 'cancelled', state.reason)
    await waitFor('survival cleanup', () => !ctx.locks.action && !ctx.locks.survival.pending)
    const gained = count('oak_log'); await delay(500); assert.equal(count('oak_log'), gained)
    await assert.rejects(call('gather_resources', defaults, old), /CANCELLED|interrupted/)
  })
  await test('gather_disconnect_interrupts_and_reconnect_does_not_resume', async () => {
    await setup('oak_log')
    const connection = ctx.manager.statusReport(), old = metadata('gather-disconnect')
    await call('gather_resources', { ...defaults, block: 'oak_log' }, old)
    await waitFor('gather digging before disconnect', () => bot.targetDigBlock)
    await call('disconnect_bot', { reason: 'Isolated gather disconnect acceptance' })
    const state = await finish()
    assert.ok(['cancelled', 'interrupted'].includes(state.phase)); assert.equal(state.returned, false)
    await call('connect_bot', { host: connection.host, port: connection.port, username, version: '26.1', auth: 'offline' })
    await waitFor('reconnected', () => ctx.manager.statusReport().status === 'online')
    bot = ctx.manager.requireBot()
    onReconnect(bot)
    await delay(1000)
    assert.equal(ctx.locks.action, null); assert.equal(bot.targetDigBlock, null)
    assert.deepEqual(await call('gathering_status'), state)
    await assert.rejects(call('gather_resources', defaults, old), /CANCELLED|interrupted/)
  })
}
