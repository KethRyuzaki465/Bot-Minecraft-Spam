'use strict'

const START_TIMEOUT_MS = 500
const RETREAT_TIMEOUT_MS = 1300
const HOP_REACH = 4.5
const HAZARDS = new Set([
  'water', 'lava', 'fire', 'soul_fire', 'magma_block', 'campfire', 'soul_campfire',
  'cactus', 'cobweb', 'sweet_berry_bush', 'powder_snow', 'wither_rose'
])

function hasRunway (bot) {
  const { position, yaw } = bot.entity
  const dx = -Math.sin(yaw)
  const dz = -Math.cos(yaw)
  for (let distance = 0; distance <= HOP_REACH; distance += 0.5) {
    for (const side of [-0.4, 0, 0.4]) {
      const feet = position.offset(dx * distance - dz * side, 0, dz * distance + dx * side).floored()
      const floor = bot.blockAt(feet.offset(0, -1, 0), false)
      if (!floor || HAZARDS.has(floor.name)) return false
      if (!floor.shapes.some(shape => shape[4] === 1)) {
        const lowerFloor = bot.blockAt(feet.offset(0, -2, 0), false)
        if (!lowerFloor || HAZARDS.has(lowerFloor.name) || !lowerFloor.shapes.some(shape => shape[4] === 1)) return false
      }
      for (let height = 0; height <= 3; height++) {
        const block = bot.blockAt(feet.offset(0, height, 0), false)
        if (!block || HAZARDS.has(block.name) || block.shapes.length > 0) return false
      }
    }
  }
  return true
}

module.exports = function runRetreatHops (bot) {
  return new Promise(resolve => {
    let running = false
    let queued = false
    let groundedTicks = 0
    const startTimer = setTimeout(() => { if (!running) finish() }, START_TIMEOUT_MS)
    const timer = setTimeout(finish, bot.health <= 6 ? 650 : RETREAT_TIMEOUT_MS)

    function finish () {
      clearTimeout(startTimer)
      clearTimeout(timer)
      bot.removeListener('physicsTick', tick)
      resolve()
    }

    function tick () {
      const entity = bot.entity
      if (!entity || bot.health <= 0 || (running && !bot.pathfinder.goal)) return finish()
      if (!entity.onGround) {
        queued = false
        groundedTicks = 0
      }
      if (!bot.pathfinder.isMoving() || !bot.getControlState('forward') || !bot.getControlState('sprint')) return
      running = true
      if (!entity.onGround || queued || entity.isInWater || bot.usingHeldItem || bot.jumpTicks > 0) return
      // Give the bot a grounded tick to regain its stride between jumps.
      if (++groundedTicks < 2) return
      if (!bot.getControlState('jump') && !hasRunway(bot)) return
      bot.setControlState('jump', true)
      queued = true
    }

    bot.on('physicsTick', tick)
  })
}
