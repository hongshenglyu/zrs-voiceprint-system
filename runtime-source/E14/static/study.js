'use strict'

const get = id => document.getElementById(id)
const modes = ['off', 'on']
const study = get('study')
const participant = get('participant')
const result = get('result')
let token = ''
let isE14 = false
let users = []
let pending = null
let busy = false
let draft = ''
const modeSelect = get('mode')

async function api(path, options = {}, credential = token) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: 'Bearer ' + credential },
    cache: 'no-store'
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败（' + response.status + '）')
  }
  return data
}

function templates(id) {
  return modes.map(mode => users.find(row => row.user_id === id + '-' + mode))
}

function refreshIdentities(selected = participant.value) {
  const ids = [...new Set(users.map(row => row.user_id.split('-')[0]))]
  participant.replaceChildren(new Option(ids.length ? '请选择已注册身份' : '暂无已注册身份', ''))
  if (draft && ids.includes(draft)) draft = ''
  for (const id of ids) participant.add(new Option(id, id))
  if (draft) participant.add(new Option(draft + ' · 待注册', draft))
  participant.value = ids.includes(selected) || draft === selected ? selected : ''
}

function render() {
  const rows = templates(participant.value)
  get('arms').textContent = participant.value
    ? modes.map((mode, i) => mode + ' ' + (rows[i] ? '已注册' : '无模板')).join(' · ')
    : '请选择已有身份或创建新身份。'
  const locked = busy || Boolean(pending)
  participant.disabled = get('create').disabled = locked
  for (const [i, option] of Array.from(modeSelect.options).entries()) {
    option.disabled = !rows[i]
  }
  if (!rows[modes.indexOf(modeSelect.value)]) {
    modeSelect.value = modes[rows.findIndex(Boolean)] || ''
  }
  modeSelect.disabled = locked || !rows.some(Boolean)
  get('enroll').querySelector('button').disabled = locked || !participant.value || rows.every(Boolean)
  get('enroll').querySelector('button').textContent = rows.some(Boolean) && !rows.every(Boolean)
    ? '重试未完成的注册' : '注册双臂'
  get('verify').querySelector('button').disabled = locked || !modeSelect.value
  get('withdraw').disabled = busy || !(pending || rows.some(Boolean))
  get('withdraw').textContent = pending ? '重试文件清理' : '撤回双臂'
}

async function run(action) {
  if (busy) return
  busy = true
  render()
  result.textContent = '处理中…'
  try {
    result.textContent = await action(participant.value)
  } catch (error) {
    result.textContent = error.message
  } finally {
    try {
      users = await api('/api/users')
      refreshIdentities()
    } catch {
      users = []
      result.textContent += '\n模板状态读取失败，请刷新页面重新解锁。'
    }
    busy = false
    render()
  }
}

get('login').addEventListener('submit', async event => {
  event.preventDefault()
  const button = event.currentTarget.querySelector('button')
  const credential = get('token').value.trim()
  button.disabled = true
  try {
    const status = await api('/api/status', {}, credential)
    const real = status.runtime_mode === 'E14'
    if (real ? !status.diagnostic_ready : (!status.authentication_ready || !status.denoise_ready)) {
      throw new Error('两臂合成策略未就绪，请检查启动终端。')
    }
    users = await api('/api/users', {}, credential)
    token = credential
    isE14 = real
    refreshIdentities()
    get('token').value = ''
    get('login').hidden = true
    study.hidden = false
    get('session').textContent = !real ? '已解锁 · off 和 on 合成策略已就绪' :
      status.authentication_ready && status.denoise_ready
        ? '已解锁 · 冻结 E14 · ' + status.policy_versions.off
        : '已解锁 · 冻结 E14 · 诊断评分模式'
    get('demo-policy-note').hidden = !(real && (status.authentication_ready || status.denoise_ready))
    if (real) {
      get('intro').textContent = '两臂共用五段已获准的注册 WAV，并分别建立模板。'
      get('clips-label').textContent = '选择五段注册 WAV'
      get('probe-label').textContent = '选择一段验证 WAV'
    }
    render()
    participant.focus()
  } catch (error) {
    get('session').textContent = error.message
  } finally {
    button.disabled = false
  }
})

get('create').addEventListener('click', () => {
  draft = crypto.randomUUID().replaceAll('-', '')
  refreshIdentities(draft)
  render()
  result.textContent = '新身份已建立，请选择注册音频。'
  get('clips').focus()
})
participant.addEventListener('change', () => {
  get('enroll').reset()
  get('verify').reset()
  result.textContent = ''
  render()
})
get('enroll').addEventListener('submit', event => {
  event.preventDefault()
  const files = Array.from(get('clips').files)
  run(async id => {
    if (files.length !== 5) throw new Error('请选择五段注册 WAV。')
    const rows = templates(id)
    for (const [i, mode] of modes.entries()) {
      if (rows[i]) continue
      const form = new FormData()
      form.set('user_id', id + '-' + mode)
      form.set('username', isE14 ? '研究参与者' : '合成参与者')
      form.set('denoise', String(mode === 'on'))
      for (const file of files) form.append('files', file)
      await api('/api/enroll', { method: 'POST', body: form })
    }
    get('enroll').reset()
    return '双臂注册完成。'
  })
})

get('verify').addEventListener('submit', event => {
  event.preventDefault()
  const file = get('probe').files[0]
  const mode = modeSelect.value
  run(async id => {
    const form = new FormData()
    form.set('user_id', id + '-' + mode)
    form.set('denoise', String(mode === 'on'))
    form.set('file', file)
    const data = await api('/api/verify', { method: 'POST', body: form })
    const model = isE14 ? '冻结 E14' : '合成演示'
    const arm = mode === 'on' ? '开启降噪' : '关闭降噪'
    const score = data.similarity.toFixed(6)
    const timing = Number.isFinite(data.processing_ms) ? ' · ' + data.processing_ms + ' ms' : ''
    if (data.accepted === null) return model + ' · ' + arm + ' · 诊断评分 ' + score + timing
    const label = isE14 ? '相似度' : '合成分数'
    return model + ' · ' + arm + ' · ' + (data.accepted ? '接受' : '拒绝') +
      '（' + label + ' ' + score + '，阈值 ' + data.threshold.toFixed(6) + '）' + timing
  })
})

get('withdraw').addEventListener('click', () => {
  if (!pending && !window.confirm('撤回此参与者的两臂模板和关联评分？')) return
  run(async id => {
    const row = pending || templates(id).find(Boolean)
    const path = '/api/users/' + encodeURIComponent(row.user_id) + '/template?' +
      new URLSearchParams({ expected_version: row.template_version })
    const data = await api(path, { method: 'DELETE' })
    pending = data.cleanup_pending ? row : null
    get('enroll').reset()
    get('verify').reset()
    return pending ? '两臂访问已撤回，文件清理待完成。请点击重试。' : '双臂已撤回，关联评分与受管文件已清理。'
  })
})
