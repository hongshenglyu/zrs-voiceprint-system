'use strict'

const get = id => document.getElementById(id)
const modes = ['off', 'on']
const study = get('study')
const participant = get('participant')
const result = get('result')
let token = ''
let isE14 = false
let users = []
let identityDataUnavailable = false
let pending = null
let busy = false
let draft = ''
let draftName = ''
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

function identityLabel(id) {
  if (!id) return ''
  if (id === draft) return draftName
  const name = templates(id).find(Boolean)?.username
  return name && !['研究参与者', '合成参与者'].includes(name) ? name : id
}

function identityOptionLabel(id) {
  const name = identityLabel(id)
  const others = new Set(users.filter(row => row.username === name).map(row => row.user_id.replace(/-(off|on)$/, '')))
  return others.size > 1 ? name + ' · ' + id.slice(-8) : name
}

function refreshIdentities(selected = participant.value) {
  const ids = [...new Set(users.map(row => row.user_id.split('-')[0]))]
  participant.replaceChildren(new Option(ids.length ? '请选择已注册身份' : '暂无已注册身份', ''))
  if (draft && ids.includes(draft)) draft = ''
  for (const id of ids) participant.add(new Option(identityOptionLabel(id), id))
  if (draft) participant.add(new Option(draftName + ' · 待保存', draft))
  participant.value = ids.includes(selected) || draft === selected ? selected : ''
}

function render() {
  const rows = templates(participant.value)
  get('arms').textContent = identityDataUnavailable ? '暂时无法读取注册状态。' : participant.value
    ? modes.map((mode, i) => (mode === 'on' ? '开启降噪' : '关闭降噪') + '：' + (rows[i] ? '已保存' : '未保存')).join(' · ')
    : '请选择已有身份或创建新身份。'
  const locked = busy || identityDataUnavailable || Boolean(pending) || Boolean(window.zrsCaptureLocked?.())
  for (const field of get('create-identity').elements) field.disabled = locked
  get('clips').disabled = get('probe').disabled = locked
  participant.disabled = get('create').disabled = locked
  for (const [i, option] of Array.from(modeSelect.options).entries()) {
    option.disabled = !rows[i]
  }
  if (!rows[modes.indexOf(modeSelect.value)]) {
    modeSelect.value = modes[rows.findIndex(Boolean)] || ''
  }
  modeSelect.disabled = locked || !rows.some(Boolean)
  get('enroll').querySelector('button[type=submit]').disabled = locked || !participant.value || rows.every(Boolean) || get('clips').files.length !== 5
  get('enroll').querySelector('button[type=submit]').textContent = rows.some(Boolean) && !rows.every(Boolean)
    ? '继续保存' : '保存声纹'
  get('verify').querySelector('button[type=submit]').disabled = locked || !modeSelect.value || !get('probe').files.length
  get('retry-identity-data').hidden = !identityDataUnavailable
  get('retry-identity-data').disabled = busy
  get('withdraw').disabled = busy || identityDataUnavailable || window.zrsCaptureLocked?.() || !(pending || rows.some(Boolean))
  get('withdraw').textContent = pending ? '继续删除' : '删除身份'
  document.dispatchEvent(new Event('zrs:controls'))
}

async function run(action) {
  if (busy || (action && identityDataUnavailable) || window.zrsCaptureLocked?.()) return
  busy = true
  render()
  result.textContent = '处理中…'
  try {
    if (action) result.textContent = await action(participant.value)
  } catch (error) {
    result.textContent = error.message
  } finally {
    try {
      users = await api('/api/users')
      identityDataUnavailable = false
      refreshIdentities()
      if (!action) result.textContent = '身份状态已更新。'
    } catch {
      identityDataUnavailable = true
      result.textContent += '\n暂时无法读取身份，请点击“重新读取身份”。如果仍不成功，请重新登录。'
    }
    busy = false
    render()
    document.dispatchEvent(new Event('zrs:data'))
  }
}

get('login').addEventListener('submit', async event => {
  event.preventDefault()
  const button = event.currentTarget.querySelector('button')
  button.disabled = true
  try {
    const reply = await fetch('/session/login', {method:'POST', body:new FormData(event.currentTarget)})
    const login = await reply.json()
    if (!reply.ok) throw new Error(login.detail)
    const credential = login.token
    const status = await api('/api/status', {}, credential)
    const real = status.runtime_mode === 'E14'
    if (real ? !status.diagnostic_ready : (!status.authentication_ready || !status.denoise_ready)) {
      throw new Error('系统暂时无法验证，请重新启动后再试。')
    }
    users = await api('/api/users', {}, credential)
    token = credential
    isE14 = real
    refreshIdentities()
    get('password').value = ''
    get('login').hidden = true
    study.hidden = false
    get('session').textContent = !real ? '已进入合成演示' :
      status.authentication_ready && status.denoise_ready
        ? '已登录 · 本地演示判定'
        : '已登录 · 只显示相似度'
    get('demo-policy-note').hidden = !(real && (status.authentication_ready || status.denoise_ready))
    if (real) {
      get('intro').textContent = '选好身份后录五段声音，再保存。'
      get('clips-label').textContent = '上传五段录音（WAV、MP3、MP4 等）'
      get('probe-label').textContent = '上传一段录音（WAV、MP3、MP4 等）'
    }
    render()
    document.dispatchEvent(new CustomEvent('zrs:ready', {detail: status}))
    get('section-dashboard')?.scrollIntoView({block:'start'})
  } catch (error) {
    get('session').textContent = error.message
  } finally {
    button.disabled = false
  }
})

get('retry-identity-data').addEventListener('click', () => run())

get('create').addEventListener('click', () => {
  location.hash = 'section-identities'
  get('create-identity').hidden = false
  get('identity-name').focus()
})
get('cancel-identity').addEventListener('click', () => {
  get('create-identity').reset()
  get('create-identity').hidden = true
  get('create').focus()
})
get('identity-name').addEventListener('input', event => event.target.setCustomValidity(''))
get('create-identity').addEventListener('submit', event => {
  event.preventDefault()
  const input = get('identity-name')
  const name = input.value.trim()
  if (!name) {
    input.setCustomValidity('请填写名字或昵称。')
    input.reportValidity()
    return
  }
  draft = crypto.randomUUID().replaceAll('-', '')
  draftName = name
  refreshIdentities(draft)
  participant.dispatchEvent(new Event('change', { bubbles: true }))
  event.currentTarget.reset()
  event.currentTarget.hidden = true
  result.textContent = isE14 ? '请录五段声音，录完点“保存声纹”。' : '请添加五段演示录音，再点“保存声纹”。'
  location.hash = 'section-register'
  get(isE14 ? 'record-enroll' : 'clips').focus()
})
participant.addEventListener('change', () => {
  get('enroll').reset()
  get('verify').reset()
  result.textContent = ''
  document.dispatchEvent(new Event('zrs:selection'))
  render()
})
get('enroll').addEventListener('submit', event => {
  event.preventDefault()
  const files = Array.from(get('clips').files)
  run(async id => {
    if (files.length !== 5) throw new Error('请上传五段录音（WAV、MP3、MP4 等）。')
    const rows = templates(id)
    for (const [i, mode] of modes.entries()) {
      if (rows[i]) continue
      const form = new FormData()
      form.set('user_id', id + '-' + mode)
      form.set('username', identityLabel(id))
      form.set('denoise', String(mode === 'on'))
      for (const file of files) form.append('files', file)
      await api('/api/enroll', { method: 'POST', body: form })
    }
    get('enroll').reset()
    return '声纹已保存，可以验证了。'
  })
})

get('verify').addEventListener('submit', event => {
  event.preventDefault()
  const file = get('probe').files[0]
  const mode = modeSelect.value
  run(async id => {
    document.dispatchEvent(new CustomEvent('zrs:outcome', {detail: null}))
    const form = new FormData()
    form.set('user_id', id + '-' + mode)
    form.set('denoise', String(mode === 'on'))
    form.set('file', file)
    const data = await api('/api/verify', { method: 'POST', body: form })
    document.dispatchEvent(new CustomEvent('zrs:outcome', {detail: data}))
    const model = isE14 ? '声纹识别' : '演示'
    const arm = mode === 'on' ? '开启降噪' : '关闭降噪'
    const score = data.similarity.toFixed(6)
    const timing = Number.isFinite(data.processing_ms) ? ' · ' + data.processing_ms + ' ms' : ''
    if (data.accepted === null) return model + ' · ' + arm + ' · 相似度（无判定） ' + score + timing
    const label = isE14 ? '相似度' : '合成分数'
    return model + ' · ' + arm + ' · ' + (data.accepted ? '验证通过' : '未通过') +
      '（' + label + ' ' + score + '，通过标准 ' + data.threshold.toFixed(6) + '）' + timing
  })
})

get('withdraw').addEventListener('click', () => {
  if (!pending && !window.confirm('删除此身份、声纹及验证记录？')) return
  run(async id => {
    const row = pending || templates(id).find(Boolean)
    const path = '/api/users/' + encodeURIComponent(row.user_id) + '/template?' +
      new URLSearchParams({ expected_version: row.template_version })
    const data = await api(path, { method: 'DELETE' })
    pending = data.cleanup_pending ? row : null
    get('enroll').reset()
    get('verify').reset()
    return pending ? '身份已停用，部分文件仍待删除，请点继续删除。' : '身份和相关记录已删除。'
  })
})
