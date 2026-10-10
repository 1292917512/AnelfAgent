// Real server cases share the isolated world and lifecycle harness in live-survival.cjs.
const assert = require('node:assert/strict')
const { setTimeout: delay } = require('node:timers/promises')

module.exports = async function supplyCases ({ test, call, command, waitFor, bot, ctx, username, metadata }) {
  const position = { x: 2, y: 64, z: 0 }
  const count = name => bot.inventory.items().filter(item => item.name === name).reduce((sum, item) => sum + item.count, 0)
  async function setup (block = 'chest') {
    await call('configure_survival', { enabled: false, intervalMs: 250 })
    await command(`setblock 2 64 0 ${block}`)
    await waitFor('container loaded', () => bot.blockAt(bot.entity.position.floored().offset(2, 0, 0))?.name === block.split('[')[0])
  }
  async function give (item, amount) {
    const before = count(item)
    await command(`give ${username} minecraft:${item} ${amount}`)
    await waitFor(`received ${item}`, () => count(item) >= before + amount)
  }
  async function stock (slot, item, amount) { await command(`item replace block 2 64 0 container.${slot} with minecraft:${item} ${amount}`) }
  async function finish () {
    let state
    await waitFor('supply terminal after cleanup', async () => {
      state = await call('supply_status'); return state.active === false
    }, 65000)
    return state
  }
  async function run (deposit = [], withdraw = [], origin) {
    const result = await call('manage_supplies', { ...position, deposit, withdraw }, origin)
    assert.equal(result.phase, 'running')
    return finish()
  }
  const clean = state => {
    assert.equal(state.inventoryClean, true, state.reason)
    assert.equal(bot.currentWindow, null)
    assert.equal(bot.inventory.selectedItem, null)
  }
  async function readChest () {
    const win = await bot.openContainer(bot.blockAt(bot.entity.position.floored().offset(2, 0, 0)))
    await bot._syncWindow(win)
    const counts = {}
    for (const item of win.slots.slice(0, win.inventoryStart).filter(Boolean)) counts[item.name] = (counts[item.name] || 0) + item.count
    await bot.closeWindow(win); await bot._syncWindow(bot.inventory)
    return counts
  }

  await test('supply_unload_and_replenish_tools_food_torches', async () => {
    await setup(); await give('cobblestone', 80); await give('bread', 2); await give('torch', 4)
    await stock(0, 'bread', 10); await stock(1, 'torch', 32); await stock(2, 'wooden_pickaxe', 1)
    const state = await run([{ item: 'cobblestone', count: 64, keep: 16 }],
      [{ item: 'bread', count: 8 }, { item: 'torch', count: 16 }, { item: 'wooden_pickaxe', count: 1 }])
    assert.equal(state.phase, 'completed', state.reason); clean(state)
    assert.equal(state.confirmed, true)
    assert.deepEqual(state.items.map(item => [item.item, item.deposited, item.withdrawn]),
      [['cobblestone', 64, 0], ['bread', 0, 6], ['torch', 0, 12], ['wooden_pickaxe', 0, 1]])
    assert.deepEqual(await readChest(), { bread: 4, torch: 20, cobblestone: 64 })
    assert.equal(count('cobblestone'), 16); assert.equal(count('bread'), 8); assert.equal(count('wooden_pickaxe'), 1)
  })
  for (const kind of ['barrel', 'double_chest']) await test(`supply_${kind}_and_already_sufficient_target`, async () => {
    await setup(kind === 'barrel' ? 'barrel' : 'chest[facing=north,type=left]')
    if (kind === 'double_chest') await command('setblock 3 64 0 chest[facing=north,type=right]')
    await give('oak_log', 5); await stock(0, 'cobblestone', 64)
    const state = await run([], [{ item: 'oak_log', count: 3 }, { item: 'cobblestone', count: 16 }])
    assert.equal(state.phase, 'completed', state.reason); clean(state)
    assert.equal(state.items[0].withdrawn, 0); assert.equal(count('oak_log'), 5)
    assert.equal(count('cobblestone'), 16)
  })
  await test('supply_shortage_rejects_before_any_deposit', async () => {
    await setup(); await give('cobblestone', 32); await stock(0, 'bread', 2)
    const state = await run([{ item: 'cobblestone', count: 32 }], [{ item: 'bread', count: 8 }])
    assert.equal(state.phase, 'blocked'); clean(state)
    assert.match(state.reason, /lacks bread/)
    assert.equal(count('cobblestone'), 32); assert.deepEqual(await readChest(), { bread: 2 })
  })
  await test('supply_full_chest_does_not_take_inventory', async () => {
    await setup(); await give('cobblestone', 32)
    const items = Array.from({ length: 27 }, (_, Slot) => ({ Slot, id: 'minecraft:dirt', count: 64 }))
    await command(`data merge block 2 64 0 {Items:${JSON.stringify(items)}}`)
    const state = await run([{ item: 'cobblestone', count: 32 }])
    assert.equal(state.phase, 'blocked', state.reason); clean(state)
    assert.match(state.reason, /Container.*insufficient/)
    assert.equal(count('cobblestone'), 32)
  })
  await test('supply_full_inventory_can_unload_then_restock', async () => {
    await setup(); await give('cobblestone', 64); await give('dirt', 2240); await stock(0, 'bread', 8)
    const blocked = await run([], [{ item: 'bread', count: 8 }])
    assert.equal(blocked.phase, 'blocked'); clean(blocked)
    const state = await run([{ item: 'cobblestone', count: 64 }], [{ item: 'bread', count: 8 }])
    assert.equal(state.phase, 'completed', state.reason); clean(state)
    assert.equal(count('bread'), 8); assert.equal(count('cobblestone'), 0); assert.equal(count('dirt'), 2240)
  })
  await test('supply_reserves_and_explicit_target_guards', async () => {
    await setup(); await give('wooden_pickaxe', 1); await give('bread', 6); await give('torch', 8); await give('oak_planks', 16)
    for (const item of ['wooden_pickaxe', 'bread', 'torch']) {
      const state = await run([{ item, count: 1 }])
      assert.equal(state.phase, 'blocked'); assert.match(state.reason, /required reserve/); clean(state)
    }
    const state = await run([{ item: 'oak_planks', count: 8, keep: 9 }])
    assert.equal(state.phase, 'blocked'); clean(state)
    await assert.rejects(call('manage_supplies', { x: 10, y: 64, z: 0, withdraw: [{ item: 'bread', count: 1 }] }), /INVALID_ARGS/)
    await command('setblock 2 64 0 trapped_chest')
    await delay(150)
    await assert.rejects(call('manage_supplies', { ...position, withdraw: [{ item: 'bread', count: 8 }] }), /INVALID_ARGS/)
  })
  await test('supply_stop_with_server_cursor_returns_to_source', async () => {
    await setup(); await stock(0, 'cobblestone', 64)
    const old = metadata('supply-before-stop'), dropped = []
    let stopping
    const spawn = entity => { if (entity.name === 'item') dropped.push(entity.id) }
    const cursor = packet => {
      if (!stopping && packet.windowId > 0 && packet.carriedItem?.itemCount > 0) stopping = call('cancel_task')
    }
    bot.on('entitySpawn', spawn); bot._client.on('window_items', cursor)
    try {
      await call('manage_supplies', { ...position, withdraw: [{ item: 'cobblestone', count: 8 }] }, old)
      await waitFor('server cursor triggers stop', () => Boolean(stopping)); await stopping
      const state = await finish()
      assert.equal(state.phase, 'cancelled', state.reason); clean(state)
      const delivered = count('cobblestone')
      assert.ok(delivered >= 0 && delivered < 8, 'Stop must interrupt the partial-stack transfer, allowing an already in-flight click')
      assert.equal(state.items[0].withdrawn, delivered)
      assert.deepEqual(await readChest(), { cobblestone: 64 - delivered })
      await delay(400); assert.deepEqual(dropped, [])
      await assert.rejects(call('manage_supplies', { ...position, withdraw: [{ item: 'cobblestone', count: 8 }] }, old), /CANCELLED|predates/)
    } finally { bot.removeListener('entitySpawn', spawn); bot._client.removeListener('window_items', cursor) }
  })
  await test('supply_cancel_keeps_confirmed_partial_deposit', async () => {
    await setup(); await give('cobblestone', 128)
    let stopping
    const cursor = packet => {
      if (!stopping && packet.windowId > 0 && packet.carriedItem?.itemCount > 0 &&
          packet.items.slice(0, 27).some(item => item.itemId === bot.registry.itemsByName.cobblestone.id && item.itemCount === 64)) stopping = call('cancel_task')
    }
    bot._client.on('window_items', cursor)
    try {
      await call('manage_supplies', { ...position, deposit: [{ item: 'cobblestone', count: 96 }] })
      await waitFor('second stack pickup triggers stop', () => Boolean(stopping)); await stopping
      const state = await finish()
      assert.equal(state.phase, 'cancelled', state.reason); clean(state)
      const deposited = state.items[0].deposited
      assert.ok(deposited >= 64 && deposited < 96, 'First stack stays delivered; the next partial stack must be interrupted')
      assert.equal(count('cobblestone'), 128 - deposited)
      assert.deepEqual(await readChest(), { cobblestone: deposited })
    } finally { bot._client.removeListener('window_items', cursor) }
  })
  await test('supply_hurt_returns_cursor_before_survival', async () => {
    await setup(); await stock(0, 'cobblestone', 64)
    await call('configure_survival', { enabled: true, intervalMs: 250 })
    const old = metadata('supply-before-hurt')
    let damage
    const cursor = packet => {
      if (!damage && packet.windowId > 0 && packet.carriedItem?.itemCount > 0) damage = command(`damage ${username} 2 minecraft:generic`)
    }
    bot._client.on('window_items', cursor)
    try {
      await call('manage_supplies', { ...position, withdraw: [{ item: 'cobblestone', count: 32 }] }, old)
      await waitFor('damage during transfer', () => Boolean(damage)); await damage
      const state = await finish()
      assert.equal(state.phase, 'cancelled', state.reason); clean(state)
      assert.equal(state.confirmed, true)
      await waitFor('survival drained', () => !ctx.locks.action && !ctx.locks.survival.pending)
      await assert.rejects(call('manage_supplies', { ...position, withdraw: [{ item: 'cobblestone', count: 32 }] }, old), /CANCELLED|interrupted/)
      assert.equal(metadata().epoch, old.epoch)
    } finally { bot._client.removeListener('window_items', cursor) }
  })
  await test('supply_named_stack_keeps_components_separate', async () => {
    await setup(); await give('cobblestone', 63)
    await command('item replace block 2 64 0 container.0 with minecraft:cobblestone[minecraft:custom_name="Supply Test"] 16')
    const state = await run([], [{ item: 'cobblestone', count: 64 }])
    assert.equal(state.phase, 'completed', state.reason); clean(state)
    const items = bot.inventory.items().filter(item => item.name === 'cobblestone')
    assert.equal(items.length, 2)
    assert.ok(items.some(item => item.count === 1 && item.components.some(component => component.type === 'custom_name')))
    assert.deepEqual(await readChest(), { cobblestone: 15 })
  })
  // Keep disconnect last: remaining harness cleanup is safe for an already disconnected bot.
  await test('supply_orderly_disconnect_recovers_server_cursor', async () => {
    await setup(); await stock(0, 'cobblestone', 64)
    let quitting
    const dropped = []
    const spawn = entity => { if (entity.name === 'item') dropped.push(entity.id) }
    const cursor = packet => {
      if (!quitting && packet.windowId > 0 && packet.carriedItem?.itemCount > 0) quitting = call('disconnect_bot', { reason: 'Supply graceful shutdown acceptance' })
    }
    bot.on('entitySpawn', spawn); bot._client.on('window_items', cursor)
    try {
      await call('manage_supplies', { ...position, withdraw: [{ item: 'cobblestone', count: 32 }] })
      await waitFor('disconnect during actual cursor ownership', () => Boolean(quitting))
      const disconnected = await quitting
      assert.equal(disconnected.inventory.restored, true, JSON.stringify(disconnected))
      const state = await finish()
      assert.equal(state.phase, 'cancelled', state.reason); clean(state)
      assert.equal(state.confirmed, true)
      assert.ok(state.items[0].withdrawn < 32)
      assert.deepEqual(dropped, [])
      assert.equal((await call('get_connection_status')).status, 'disconnected')
      assert.equal(ctx.locks.action, null)
    } finally { bot.removeListener('entitySpawn', spawn); bot._client.removeListener('window_items', cursor) }
  })
}
