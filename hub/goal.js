/**
 * POKE Hub — Goal-Based Autonomous Control
 *
 * User sets a goal → LLM plans → deploys code → monitors → adjusts.
 * Continuous loop until goal is met or cancelled.
 */

const { log } = require('./logger')
const { nodes } = require('./nodes')
const { agentLoop } = require('./agent')
const trace = require('./trace')

const activeGoals = new Map()  // edgeId → goal state
const MAX_CONSECUTIVE_MET = 3  // goal met N times in a row → auto-complete

/**
 * Start a goal-based control loop.
 * @param {string} edgeId - Target edge
 * @param {string} goalText - Natural language goal from user
 * @param {number} checkInterval - ms between checks (default 10s)
 */
async function startGoal(edgeId, goalText, checkInterval = 10000) {
  const node = nodes.get(edgeId)
  if (!node) throw new Error(`Edge ${edgeId} not found`)

  const goal = {
    id: Date.now().toString(36),
    edge: edgeId,
    text: goalText,
    status: 'initializing',
    startTime: new Date().toISOString(),
    cycles: 0,
    consecutiveMet: 0,
    metrics: [],     // {cycle, temp, cpu, power, ...}
    history: [],
    interval: checkInterval,
    timer: null,
  }

  activeGoals.set(edgeId, goal)
  trace.emit('goal_start', { edge: edgeId, goal: goalText })
  log.info(`[goal] starting: "${goalText}" on ${edgeId}`)

  // Phase 1: Initial planning — LLM analyzes goal and sets up
  goal.status = 'planning'
  try {
    const planCommand = `GOAL MODE: You are managing edge "${edgeId}". ` +
      `User's goal: "${goalText}". ` +
      `Current edge state is available via health endpoint. ` +
      `Virtual registers at 0x200000: +0=temp, +4=cpu_load, +8=cooling(0/1/2), +12=power, ` +
      `+16=web_srv, +20=db_srv, +24=cache_srv, +28=log_srv, +32=backup_srv, +36=dev_srv, +40=alert. ` +
      `STEP 1: Read current state from edge. ` +
      `STEP 2: Deploy monitors or residents if continuous monitoring needed. ` +
      `STEP 3: Take immediate corrective actions if current state doesn't meet goal. ` +
      `Use execute_x86 with: BITS 32 / mov dword [0x200000+offset], value / ret`

    const result = await agentLoop(planCommand, edgeId, edgeId)
    goal.history.push({
      cycle: 0, phase: 'plan', time: new Date().toISOString(),
      actions: result.steps.map(s => s.tool),
      result: result.result?.slice(0, 200),
    })
    trace.emit('goal_plan', { edge: edgeId, steps: result.steps.length })
  } catch (e) {
    goal.status = 'error'
    goal.history.push({ cycle: 0, phase: 'plan', error: e.message })
    log.warn(`[goal] planning failed: ${e.message}`)
    return goal
  }

  // Phase 2: Monitoring loop
  goal.status = 'active'
  goal.timer = setInterval(async () => {
    if (goal.status !== 'active') return
    goal.cycles++

    try {
      // Read current state
      const health = node.health
      if (!health) return

      const vr = health.vr || {}
      const state = `temp=${vr.t}, cpu=${vr.c}, cooling=${vr.co}, power=${vr.p}, ` +
        `srv=${vr.srv}, alert=${vr.a}`

      // Ask LLM to evaluate and adjust
      const evalCommand = `GOAL CHECK (cycle ${goal.cycles}): ` +
        `Goal: "${goalText}". ` +
        `Current state: ${state}. ` +
        `Edge: ${edgeId}. ` +
        `Is the goal being met? If NOT, take corrective action. ` +
        `If goal IS met, reply "GOAL_MET" and explain. ` +
        `Keep actions minimal — only adjust what's needed. ` +
        `Virtual registers: 0x200000+0=temp,+4=cpu,+8=cooling(0/1/2),+12=power,+16~36=servers,+40=alert. ` +
        `Use execute_x86: BITS 32 / mov dword [0x200000+offset], value / ret`

      const result = await agentLoop(evalCommand, edgeId, edgeId)

      const entry = {
        cycle: goal.cycles,
        time: new Date().toISOString(),
        state,
        actions: result.steps.map(s => ({ tool: s.tool, target: s.input?.target })),
        result: result.result?.slice(0, 200),
        goalMet: result.result?.includes('GOAL_MET') || false,
      }
      goal.history.push(entry)
      trace.emit('goal_check', { edge: edgeId, cycle: goal.cycles, goalMet: entry.goalMet, actions: entry.actions.length })
      // Store goal cycle as structured context
      try { require('./context').storeGoal(edgeId, `C${goal.cycles}: ${entry.goalMet ? 'MET' : state}`).catch(() => {}) } catch(e) {}

      // Track metrics
      if (vr) {
        goal.metrics.push({ cycle: goal.cycles, t: vr.t, c: vr.c, p: vr.p, co: vr.co })
        if (goal.metrics.length > 100) goal.metrics = goal.metrics.slice(-100)
      }

      // Track consecutive goal met
      if (entry.goalMet) {
        goal.consecutiveMet++
        log.info(`[goal] met at cycle ${goal.cycles} (${goal.consecutiveMet}/${MAX_CONSECUTIVE_MET})`)
        if (goal.consecutiveMet >= MAX_CONSECUTIVE_MET) {
          goal.status = 'completed'
          if (goal.timer) clearInterval(goal.timer)
          log.info(`[goal] AUTO-COMPLETED after ${goal.cycles} cycles`)
          trace.emit('goal_complete', { edge: edgeId, cycles: goal.cycles })
        }
      } else {
        goal.consecutiveMet = 0
      }
    } catch (e) {
      log.warn(`[goal] check failed cycle ${goal.cycles}: ${e.message}`)
    }
  }, checkInterval)

  return goal
}

function stopGoal(edgeId) {
  const goal = activeGoals.get(edgeId)
  if (!goal) return null
  if (goal.timer) clearInterval(goal.timer)
  goal.status = 'stopped'
  goal.endTime = new Date().toISOString()
  trace.emit('goal_stop', { edge: edgeId, cycles: goal.cycles })
  log.info(`[goal] stopped on ${edgeId} after ${goal.cycles} cycles`)
  return goal
}

function getGoal(edgeId) {
  return activeGoals.get(edgeId) || null
}

function listGoals() {
  return [...activeGoals.entries()].map(([id, g]) => ({
    edge: id,
    goal: g.text,
    status: g.status,
    cycles: g.cycles,
    started: g.startTime,
  }))
}

module.exports = { startGoal, stopGoal, getGoal, listGoals }
