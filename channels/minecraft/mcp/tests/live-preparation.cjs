// Explicit-area gathering and crafting share one real action against the isolated server.
const assert = require('node:assert/strict')
const { setTimeout: delay } = require('node:timers/promises')

module.exports = async function preparationCases ({ test, call, command, waitFor, bot, ctx, username, metadata, onReconnect }) {
  const count = name => bot.inventory.items().filter(item => item.name === name).reduce((n, item) => n + item.count, 0)
  const block = (x, y = 64, z = 0) => bot.blockAt(bot.entity.position.clone().set(x, y, z))
  const area = { block: 'oak_log', x: 5, y: 64, z: 0, radius: 3, maxCount: 8 }
  const defaults = { item: 'wooden_pickaxe', count: 1, mode: 'craft', gather: area }
  async function setup (wood = 'oak_log') {
    await call('configure_survival', { enabled: false, intervalMs: 250 })
    await command(`fill 4 64 0 7 64 0 ${wood}`)
    await waitFor('loaded logs', () => block(4)?.name === wood && block(7)?.name === wood)
  }
  async function give (name, amount) {
    const before = count(name)
    await command(`give ${username} ${name} ${amount}`)
    await waitFor('inventory received', () => count(name) === before + amount)
  }
  async function finish () {
    let state
    await waitFor('preparation terminal', async () => { state = await call('production_status'); return !state.active }, 100000)
    return state
  }
  async function run (args = {}) {
    const admitted = await call('prepare_item', { ...defaults, ...args })
    assert.equal(admitted.phase, 'running')
    const state = await finish()
    assert.equal(state.id, admitted.id); assert.equal(state.actionId, admitted.actionId)
    assert.equal(ctx.locks.action, null, 'All dependent work must drain before terminal publication')
    return state
  }
  async function table () {
    await command('setblock 2 64 0 crafting_table')
    await waitFor('table loaded', () => block(2)?.name === 'crafting_table')
  }
  const clean = state => {
    assert.equal(state.inventoryClean, true, state.reason)
    assert.equal(bot.inventory.selectedItem, null)
    assert.equal(bot.currentWindow, null)
  }

  for (const wood of ['oak_log', 'birch_log']) await test(`preparation_${wood}_empty_inventory_to_pickaxe`, async () => {
    await setup(wood)
    const state = await run({ gather: { ...area, block: wood } })
    assert.equal(state.phase, 'completed', state.reason); clean(state)
    assert.equal(state.plannedGather, 3); assert.equal(state.gathering.gained, 3)
    assert.equal(state.gathering.returned, true); assert.equal(state.created, 1)
    assert.equal(state.table.placed, true); assert.equal(count('wooden_pickaxe'), 1)
    assert.equal(block(7).name, wood, 'Only the recipe deficit may be harvested')
    assert.equal(bot.entity.position.floored().toString(), '(0, 64, 0)')
    const events = await call('get_events', { since: 0, types: ['production_progress', 'gather_progress'], limit: 100 })
    const owned = events.events.filter(event => event.data.actionId === state.actionId)
    assert.equal(owned.length, 1); assert.equal(owned[0].type, 'production_progress')
  })
  await test('preparation_vertical_trunk_with_leaves', async () => {
    await setup(); await command('fill 4 64 0 7 64 0 air')
    await command('fill 4 64 0 4 66 0 oak_log')
    await command('fill 3 67 -1 5 67 1 oak_leaves[persistent=true]')
    await waitFor('tree loaded', () => block(4, 66).name === 'oak_log')
    const state = await run()
    assert.equal(state.phase, 'completed', state.reason); clean(state)
    assert.equal(state.gathering.gained, 3); assert.equal(count('wooden_pickaxe'), 1)
    assert.equal(block(4, 63).name, 'stone'); assert.equal(block(4, 67).name, 'oak_leaves')
  })
  await test('preparation_collects_only_one_missing_log', async () => {
    await setup(); await give('oak_log', 2)
    const state = await run()
    assert.equal(state.phase, 'completed', state.reason); assert.equal(state.plannedGather, 1)
    assert.equal(state.gathering.gained, 1)
    assert.equal([4, 5, 6, 7].filter(x => block(x).name === 'oak_log').length, 3); clean(state)
  })
  await test('preparation_existing_table_reduces_gathering', async () => {
    await setup(); await table()
    const state = await run()
    assert.equal(state.phase, 'completed', state.reason); assert.equal(state.plannedGather, 2)
    assert.equal(state.table.reused, true); assert.equal(state.table.placed, false); clean(state)
  })
  await test('preparation_sufficient_materials_do_not_gather', async () => {
    await setup(); await give('oak_log', 3)
    const state = await run()
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.plannedGather, 0); assert.equal(state.gathering, null)
    assert.equal(block(4).name, 'oak_log'); assert.equal(state.created, 1); clean(state)
  })
  await test('preparation_existing_tool_ensure_and_additional_craft', async () => {
    await setup(); await give('wooden_pickaxe', 1)
    const reuse = await run({ mode: 'ensure' })
    assert.equal(reuse.phase, 'completed', reuse.reason); assert.equal(reuse.created, 0)
    assert.equal(reuse.reused, 1); assert.equal(reuse.plannedGather, 0); assert.equal(block(4).name, 'oak_log')
    const extra = await run()
    assert.equal(extra.phase, 'completed', extra.reason); assert.equal(extra.created, 1)
    assert.equal(extra.available, 2); assert.equal(extra.reused, 0); clean(extra)
  })
  await test('preparation_limit_rejects_before_gathering', async () => {
    await setup()
    const state = await run({ gather: { ...area, maxCount: 2 } })
    assert.equal(state.phase, 'blocked'); assert.match(state.reason, /authorized 2/)
    assert.equal(state.gathering, null); assert.equal(count('oak_log'), 0); assert.equal(block(4).name, 'oak_log')
  })
  await test('preparation_full_inventory_rejects_before_gathering', async () => {
    await setup(); await give('dirt', 2304)
    const state = await run()
    assert.equal(state.phase, 'blocked'); assert.equal(state.gathering, null)
    assert.equal(block(4).name, 'oak_log'); clean(state)
  })
  await test('preparation_no_table_space_rejects_before_gathering', async () => {
    await setup(); await command('fill -3 64 -3 3 66 3 stone')
    await command('fill 0 64 0 0 65 0 air')
    await waitFor('placement obstruction loaded', () => block(1, 65).name === 'stone')
    const state = await run()
    assert.equal(state.phase, 'blocked'); assert.match(state.reason, /No safe workbench space/)
    assert.equal(state.gathering, null); assert.equal(block(4).name, 'oak_log')
  })
  await test('preparation_partial_gather_never_consumes_remaining_logs', async () => {
    await setup(); await command('fill 5 64 0 7 64 0 air')
    await waitFor('limited resource area', () => block(5).name === 'air')
    const state = await run()
    assert.equal(state.phase, 'blocked'); assert.equal(state.gathering.gained, 1)
    assert.equal(state.gathering.returned, true); assert.equal(count('oak_log'), 1)
    assert.equal(count('oak_planks'), 0); assert.equal(state.created, 0); clean(state)
  })
  await test('preparation_no_pickup_never_starts_crafting', async () => {
    await setup(); await command('gamerule minecraft:block_drops false')
    try {
      const state = await run()
      assert.equal(state.phase, 'blocked'); assert.equal(state.gathering.dug, 1)
      assert.equal(state.gathering.gained, 0); assert.equal(state.created, 0); assert.equal(count('oak_planks'), 0)
    } finally { await command('gamerule minecraft:block_drops true') }
  })
  await test('preparation_stop_during_gather_cannot_start_production', async () => {
    await setup()
    const old = metadata('preparation-gather-stop')
    await call('prepare_item', defaults, old)
    await waitFor('nested gathering dig', () => bot.targetDigBlock)
    await call('cancel_task')
    const state = await finish()
    assert.equal(state.phase, 'cancelled'); assert.equal(state.created, 0)
    assert.equal(count('oak_planks'), 0); assert.equal(state.gathering.returned, false); clean(state)
    await delay(500); assert.equal(count('wooden_pickaxe'), 0)
    await assert.rejects(call('prepare_item', defaults, old), /CANCELLED/)
  })
  await test('preparation_return_blocked_prevents_crafting', async () => {
    await setup(); await call('prepare_item', defaults)
    await waitFor('gathering away from start', () => bot.entity.position.x > 2.5)
    await command('setblock 0 64 0 bedrock')
    const state = await finish()
    assert.equal(state.phase, 'blocked'); assert.equal(state.gathering.returned, false)
    assert.equal(state.created, 0); assert.equal(count('oak_planks'), 0)
    assert.equal(count('oak_log'), state.gathering.gained)
    assert.ok(state.gathering.gained < 3, 'Lost return route must stop further collection')
    assert.equal(block(0).name, 'bedrock')
  })
  await test('preparation_lost_table_replans_from_actual_materials', async () => {
    await setup(); await table(); await call('prepare_item', defaults)
    await waitFor('collecting with table assumed', () => bot.targetDigBlock)
    await command('setblock 2 64 0 air')
    const state = await finish()
    assert.equal(state.phase, 'blocked'); assert.equal(state.gathering.gained, 2)
    assert.equal(state.gathering.returned, true); assert.equal(state.created, 0)
    assert.equal(count('oak_log'), 2); assert.equal(count('oak_planks'), 0)
    assert.equal([4, 5, 6, 7].filter(x => block(x).name === 'oak_log').length, 2, 'A lost facility must not silently expand the gather plan')
  })
  await test('preparation_stop_in_nested_craft_recovers_real_grid', async () => {
    await setup()
    let stopping
    const grid = packet => {
      if (!stopping && packet.windowId === 0 && packet.items.slice(1, 5).some(item => item.itemCount > 0)) stopping = call('cancel_task')
    }
    bot._client.on('window_items', grid)
    try {
      await call('prepare_item', defaults)
      await waitFor('server grid after collecting', () => Boolean(stopping), 60000)
      await stopping
      const state = await finish()
      assert.equal(state.phase, 'cancelled'); assert.equal(state.gathering.gained, 3)
      assert.equal(state.gathering.returned, true); assert.equal(state.created, 0); clean(state)
      assert.ok(count('oak_log') * 4 + count('oak_planks') >= 12)
    } finally { bot._client.removeListener('window_items', grid) }
  })
  await test('preparation_hurt_cancels_entire_composition', async () => {
    await setup(); await call('configure_survival', { enabled: true, intervalMs: 250 })
    const old = metadata('preparation-hurt')
    await call('prepare_item', defaults, old)
    await waitFor('gathering before hurt', () => bot.targetDigBlock)
    await command(`damage ${username} 2 minecraft:generic`)
    const state = await finish()
    assert.equal(state.phase, 'cancelled'); assert.equal(state.created, 0)
    await waitFor('survival drained', () => !ctx.locks.action && !ctx.locks.survival.pending)
    await assert.rejects(call('prepare_item', defaults, old), /CANCELLED|interrupted/)
    assert.equal(count('wooden_pickaxe'), 0)
  })
  await test('preparation_disconnect_never_resumes_crafting', async () => {
    await setup()
    const connection = ctx.manager.statusReport(), old = metadata('preparation-disconnect')
    await call('prepare_item', defaults, old)
    await waitFor('collecting before disconnect', () => bot.targetDigBlock)
    await call('disconnect_bot', { reason: 'Isolated preparation acceptance' })
    const state = await finish()
    assert.ok(['cancelled', 'interrupted'].includes(state.phase)); assert.equal(state.created, 0)
    await call('connect_bot', { host: connection.host, port: connection.port, username, version: '26.1', auth: 'offline' })
    await waitFor('reconnected', () => ctx.manager.statusReport().status === 'online')
    bot = ctx.manager.requireBot(); onReconnect(bot)
    await delay(1000)
    assert.deepEqual(await call('production_status'), state); assert.equal(ctx.locks.action, null)
    assert.equal(count('wooden_pickaxe'), 0)
    await assert.rejects(call('prepare_item', defaults, old), /CANCELLED|interrupted/)
  })
}
