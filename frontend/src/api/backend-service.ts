import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export async function fetchSnapshot(key: string): Promise<Record<string, any>> {
  const response = await request(`/api/snapshot/${key}`)
  if (!response.ok) throw new Error('跨模块快照读取失败')
  return await response.json()
}

/** 传输确认结果：报告归属、回执、旧轮与补充轮引用凭据。 */
export async function fetchTransferConfirmation(id: number): Promise<Record<string, any>> {
  const response = await request(`/api/data_transfer/${id}/confirmation`)
  if (!response.ok) throw new Error('传输确认结果读取失败')
  return await response.json()
}

/** 成果汇编详情：仅本报告当前版本的成果签收回执与实际到件。 */
export async function fetchCompileReceipts(id: number): Promise<Record<string, any>> {
  const response = await request(`/api/results_compile/${id}/receipts`)
  if (!response.ok) throw new Error('成果签收回执读取失败')
  return await response.json()
}

/** 按报告/传输查回执，供值班员核对文件归属。 */
export async function fetchReceipts(params: {
  reportId?: number
  transferId?: number
  status?: string
}): Promise<Record<string, any>> {
  const query = new URLSearchParams()
  if (params.reportId != null) query.set('report_id', String(params.reportId))
  if (params.transferId != null) query.set('transfer_id', String(params.transferId))
  if (params.status) query.set('status', params.status)
  const response = await request(`/api/receipts?${query.toString()}`)
  if (!response.ok) throw new Error('签收回执读取失败')
  return await response.json()
}
