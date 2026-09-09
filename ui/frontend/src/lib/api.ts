/**
 * Typed fetch wrapper for the Pick-a-Recipe REST API.
 *
 * Session-cookie auth (Flask session). A 401 anywhere means "not logged in"
 * — we redirect to /login, which is served by Flask and starts OIDC.
 */

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown; handle401?: boolean },
): Promise<T> {
  const { json, handle401, ...rest } = init ?? {}
  const res = await fetch(path, {
    credentials: 'same-origin',
    ...rest,
    headers: {
      ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...rest.headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  })

  // handle401 opts out of the redirect. The sign-in call needs to: a 401 there
  // means the password was wrong, and bouncing to /login would reload the page
  // and discard the message explaining why.
  if (res.status === 401 && !handle401) {
    // Session expired / not logged in → let the server-rendered login take over
    window.location.href = '/login'
    throw new ApiError(401, 'Not authenticated')
  }

  const data = await res.json().catch(() => null)

  if (!res.ok) {
    const message =
      (data && typeof data === 'object' && 'error' in data
        ? String((data as { error: unknown }).error)
        : null) ?? `Request failed (${res.status})`
    throw new ApiError(res.status, message)
  }

  return data as T
}

export const api = {
  // ===== Jobs =====
  createJob: (url: string) =>
    request<import('@/types').StartJobResult>('/api/jobs', {
      method: 'POST',
      json: { url },
    }),

  createJobsBatch: (urls: string[]) =>
    request<import('@/types').BatchStartResult>('/api/jobs/batch', {
      method: 'POST',
      json: { urls },
    }),

  retryJob: (params: { url?: string; history_id?: number }) =>
    request<import('@/types').StartJobResult>('/api/jobs/retry', {
      method: 'POST',
      json: params,
    }),

  getQueueStats: () => request<import('@/types').QueueStats>('/api/jobs/queue'),

  listJobs: () =>
    request<{ jobs: import('@/types').Job[] }>('/api/jobs'),

  getJob: (jobId: string) =>
    request<import('@/types').Job>(`/api/jobs/${encodeURIComponent(jobId)}`),

  cancelJob: (jobId: string) =>
    request<{ status: string; job_id: string }>(
      `/api/jobs/${encodeURIComponent(jobId)}`,
      { method: 'DELETE' },
    ),

  deleteJobEntry: (jobId: string) =>
    request<{ status: string; job_id: string }>(
      `/api/jobs/${encodeURIComponent(jobId)}/delete`,
      { method: 'DELETE' },
    ),

  setJobPriority: (jobId: string, priority: number) =>
    request<{ job_id: string; priority: number }>(
      `/api/jobs/${encodeURIComponent(jobId)}/priority`,
      { method: 'PATCH', json: { priority } },
    ),

  // ===== Tasks (unified dashboard) =====
  listTasks: (params: {
    scope?: 'mine' | 'all'
    state?: import('@/types').TaskStateGroup
    limit?: number
    offset?: number
  }) => {
    const q = new URLSearchParams()
    if (params.scope) q.set('scope', params.scope)
    if (params.state) q.set('state', params.state)
    if (params.limit !== undefined) q.set('limit', String(params.limit))
    if (params.offset !== undefined) q.set('offset', String(params.offset))
    return request<{
      tasks: import('@/types').Job[]
      counts: Record<string, number>
    }>(`/api/tasks?${q.toString()}`)
  },

  bulkTaskAction: (action: import('@/types').BulkAction, ids: string[]) =>
    request<import('@/types').BulkResult>('/api/tasks/bulk', {
      method: 'POST',
      json: { action, ids },
    }),

  // ===== History =====
  getHistory: (params?: {
    limit?: number
    offset?: number
    status?: string
    search?: string
  }) => {
    const q = new URLSearchParams()
    if (params?.limit !== undefined) q.set('limit', String(params.limit))
    if (params?.offset !== undefined) q.set('offset', String(params.offset))
    if (params?.status) q.set('status', params.status)
    if (params?.search) q.set('search', params.search)
    const qs = q.toString()
    return request<import('@/types').Paginated<import('@/types').HistoryEntry>>(
      `/api/history${qs ? `?${qs}` : ''}`,
    )
  },

  getHistoryItem: (id: number) =>
    request<import('@/types').HistoryEntry>(`/api/history/${id}`),

  deleteHistoryItem: (id: number) =>
    request<{ status: string; id: number }>(`/api/history/${id}`, {
      method: 'DELETE',
    }),

  bulkDelete: (params: { history_ids?: number[]; job_ids?: string[] }) =>
    request<{
      status: string
      deleted_count: number
      deleted_history: number
      deleted_jobs: number
    }>('/api/history/bulk-delete', { method: 'POST', json: params }),

  reuploadRecipe: (historyId: number, target?: string) =>
    request<import('@/types').ReuploadResult>(
      `/api/history/${historyId}/reupload`,
      { method: 'POST', json: target ? { target } : {} },
    ),

  // ===== Combined recipes view =====
  getRecipes: (params?: {
    limit?: number
    offset?: number
    status?: string
    search?: string
  }) => {
    const q = new URLSearchParams()
    if (params?.limit !== undefined) q.set('limit', String(params.limit))
    if (params?.offset !== undefined) q.set('offset', String(params.offset))
    if (params?.status) q.set('status', params.status)
    if (params?.search) q.set('search', params.search)
    const qs = q.toString()
    return request<import('@/types').Paginated<import('@/types').CombinedItem>>(
      `/api/recipes${qs ? `?${qs}` : ''}`,
    )
  },

  // ===== Pending uploads / approvals =====
  getPendingUploads: () =>
    request<{ pending_uploads: import('@/types').PendingUpload[] }>(
      '/api/pending-uploads',
    ),

  getPendingUpload: (uploadId: string) =>
    request<import('@/types').PendingUpload>(
      `/api/pending-uploads/${encodeURIComponent(uploadId)}`,
    ),

  confirmPendingUpload: (
    uploadId: string,
    opts?: {
      selected_image_index?: number | null
      recipe?: import('@/types').RecipeData
    },
  ) => {
    const json: Record<string, unknown> = {}
    if (opts?.selected_image_index !== undefined && opts.selected_image_index !== null) {
      json.selected_image_index = opts.selected_image_index
    }
    if (opts?.recipe) {
      json.recipe = opts.recipe
    }
    return request<{ status: string; upload_id: string; job_id: string }>(
      `/api/pending-uploads/${encodeURIComponent(uploadId)}/confirm`,
      { method: 'POST', json },
    )
  },

  cancelPendingUpload: (uploadId: string) =>
    request<{ status: string; upload_id: string; job_id: string }>(
      `/api/pending-uploads/${encodeURIComponent(uploadId)}/cancel`,
      { method: 'POST', json: {} },
    ),

  // ===== Config =====
  getConfig: () => request<import('@/types').AppConfig>('/api/config'),

  saveConfig: (patch: Partial<import('@/types').AppConfig>) =>
    request<{ status: string; saved_keys: string[] }>('/api/config', {
      method: 'POST',
      json: patch,
    }),

  exportSettings: () =>
    request<import('@/types').SettingsExport>('/api/settings/export'),

  importSettings: (payload: unknown) =>
    request<{ status: string; message: string; imported_keys: string[] }>(
      '/api/settings/import',
      { method: 'POST', json: payload },
    ),

  uploadCookies: (file: File) => {
    const form = new FormData()
    form.append('cookies_file', file)
    return request<{ status: string; message: string; path: string }>(
      '/api/cookies/upload',
      { method: 'POST', body: form },
    )
  },

  deleteCookies: () =>
    request<{ status: string; message: string }>('/api/cookies/delete', {
      method: 'DELETE',
    }),

  // ===== Push notifications =====
  pushSubscribe: (subscription: PushSubscriptionJSON) =>
    request<{ status: string }>('/api/push/subscribe', {
      method: 'POST',
      json: { subscription },
    }),

  pushUnsubscribe: (endpoint: string) =>
    request<{ status: string }>('/api/push/unsubscribe', {
      method: 'POST',
      json: { endpoint },
    }),

  // ===== Session =====
  me: () => request<import('@/types').SessionUser>('/api/me'),

  // ===== Auth =====
  authStatus: () =>
    request<{
      auth_mode: 'local' | 'authentik'
      local_auth_enabled: boolean
      sso_enabled: boolean
      setup_required: boolean
      mobile_auth_enabled: boolean
      auth_disabled: boolean
    }>('/api/auth/status'),

  localLogin: (username: string, password: string) =>
    request<{ user: string; is_admin: boolean }>('/auth/local/login', {
      method: 'POST',
      json: { username, password },
      handle401: true,
    }),

  // ===== User administration (admin only, local mode only) =====
  listUsers: () =>
    request<{ users: import('@/types').ManagedUser[]; admin_count: number }>(
      '/api/users',
    ),

  createUser: (username: string, password: string, isAdmin: boolean) =>
    request<{ user: import('@/types').ManagedUser }>('/api/users', {
      method: 'POST',
      json: { username, password, is_admin: isAdmin },
    }),

  setUserAdmin: (username: string, isAdmin: boolean) =>
    request<{ user: import('@/types').ManagedUser }>(
      `/api/users/${encodeURIComponent(username)}`,
      { method: 'PATCH', json: { is_admin: isAdmin } },
    ),

  resetUserPassword: (username: string, password: string) =>
    request<{ user: import('@/types').ManagedUser }>(
      `/api/users/${encodeURIComponent(username)}`,
      { method: 'PATCH', json: { password } },
    ),

  deleteUser: (username: string) =>
    request<{ status: string; username: string }>(
      `/api/users/${encodeURIComponent(username)}`,
      { method: 'DELETE' },
    ),

  // ===== Own account =====
  // handle401 is off: a 401 here means the session really is gone, and the
  // redirect to /login is the right answer. A wrong current password is a 403.
  changeOwnPassword: (currentPassword: string, newPassword: string) =>
    request<{ status: string }>('/api/me/password', {
      method: 'POST',
      json: { current_password: currentPassword, new_password: newPassword },
    }),

  // ===== First-run setup =====
  // Only answers while the instance has no account; 409 once one exists.
  setupInfo: () =>
    request<{ suggested_username: string; adopting: string | null }>('/api/setup'),

  createFirstAccount: (
    username: string,
    password: string,
    confirmPassword: string,
  ) =>
    request<{ user: string; is_admin: boolean }>('/api/setup', {
      method: 'POST',
      json: { username, password, confirm_password: confirmPassword },
    }),
}
