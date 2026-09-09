/**
 * TypeScript contract for the Pick-a-Recipe API.
 *
 * Mirrors ui/app.py, ui/database.py, ui/job_manager.py exactly.
 * All timestamps are SQLite "YYYY-MM-DD HH:MM:SS" strings unless noted.
 */

// ===== Enums =====

export type JobStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'uploading'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'expired'

export type PipelineStage =
  | 'queued'
  | 'pending'
  | 'info'
  | 'download'
  | 'transcribe'
  | 'visual'
  | 'image'
  | 'evaluate'
  | 'preview'
  | 'upload'
  | 'complete'
  | 'error'
  | 'cancelled'

export const PIPELINE_STAGES: PipelineStage[] = [
  'info',
  'download',
  'transcribe',
  'visual',
  'image',
  'evaluate',
  'upload',
]

export type TaskStateGroup =
  | 'pending'
  | 'processing'
  | 'awaiting_approval'
  | 'active'
  | 'recent'
  | 'all'

export type BulkAction = 'cancel' | 'approve' | 'reject'

// ===== Job =====

/** Row from recipe_jobs table (+computed fields). See database.get_job(). */
export interface Job {
  id: string
  url: string
  status: JobStatus
  progress: number // 0-100
  current_stage: PipelineStage | null
  stage_message: string | null
  video_title: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  retry_from_history_id: number | null
  llm_tokens_used: number
  queue_priority: number
  /** Username string (users.username), not a numeric id */
  user_id: string | null
  attempts: number
  next_run_at: string | null
  lease_expires_at: string | null
  state_changed_at: string | null
  /** Computed — only present when status === 'queued' */
  queue_position?: number
  /** Computed — only present when status === 'awaiting_approval' */
  pending_upload_id?: string
  approval_expires_at?: string | null
}

export interface QueueStats {
  max_concurrent: number
  queued_count: number
  active_count: number
  running_count: number
}

// ===== Recipe data (Schema.org JSON-LD from chef._postprocess_recipe) =====

export interface StructuredIngredient {
  food: string
  quantity: string
  unit: string
  notes: string
  /** Display string */
  raw: string
}

export interface HowToStep {
  '@type': 'HowToStep'
  text: string
  name?: string | null
  description?: string | null
}

export interface HowToSection {
  '@type': 'HowToSection'
  name: string
  itemListElement: { '@type': 'HowToStep'; text: string }[]
}

export type Instruction = string | HowToStep | HowToSection

export interface NutritionInfo {
  '@type': 'NutritionInformation'
  calories?: string | null
  proteinContent?: string | null
  fatContent?: string | null
  carbohydrateContent?: string | null
  fiberContent?: string | null
  sugarContent?: string | null
  sodiumContent?: string | null
  cholesterolContent?: string | null
}

export interface RecipeData {
  '@context': string
  '@type': 'Recipe'
  url: string
  video?: { '@type': 'VideoObject'; url: string }
  datePublished?: string
  name: string
  description?: string | null
  recipeYield?: string | null
  prepTime?: string | null
  cookTime?: string | null
  totalTime?: string | null
  /** Structured ingredients — internal format (plural key!) */
  recipeIngredients?: StructuredIngredient[]
  /** Flattened ingredient strings — Schema.org standard (singular key!) */
  recipeIngredient?: string[]
  recipeInstructions?: Instruction[]
  recipeCategory?: string[] | string | null
  recipeCuisine?: string[] | string | null
  keywords?: string[] | string | null
  nutrition?: NutritionInfo | null
  [key: string]: unknown
}

// ===== History =====

export interface HistoryEntry {
  id: number
  job_id: string | null
  url: string
  video_title: string | null
  recipe_name: string | null
  recipe_data: RecipeData | null
  /** Filesystem path — unreliable across restarts; prefer thumbnail_data */
  thumbnail_path: string | null
  /** base64 JPEG — persisted; prefer over thumbnail_path */
  thumbnail_data: string | null
  status: 'success' | 'failed'
  error_message: string | null
  output_target: string | null
  created_at: string
}

export interface Paginated<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

// ===== Combined history+jobs view =====

export interface CombinedItem {
  source_type: 'history' | 'job'
  job_id: string | null
  url: string
  video_title: string | null
  status: JobStatus | 'success' | 'failed'
  error_message: string | null
  created_at: string
  updated_at: string
  // history-only fields (null on jobs)
  id: number | null
  recipe_name: string | null
  recipe_data: RecipeData | null
  thumbnail_path: string | null
  thumbnail_data: string | null
  output_target: string | null
  // job-only fields (null on history)
  progress: number | null
  current_stage: PipelineStage | null
  stage_message: string | null
}

// ===== Pending uploads / approvals =====

export interface CandidateImage {
  index: number
  /** base64 JPEG */
  data: string
  path: string
  is_best: boolean
}

export interface PendingUpload {
  upload_id: string
  job_id: string
  recipe: RecipeData
  /** Pretty display label, e.g. "Tandoor" */
  output_target: string
  best_image_index: number
  selected_image_index: number
  url?: string | null
  video_title?: string | null
  created_at?: string | null
  expires_at?: string | null
  image_data?: string
  candidate_images: CandidateImage[]
}

// ===== Job creation =====

export interface StartJobResult {
  job_id: string
  status: JobStatus
  url: string
  queue_position: number
  message: string
  auto_start?: boolean
}

export interface BatchStartResult {
  jobs: StartJobResult[]
  count: number
}

export interface BulkResult {
  results: { id: string; ok: boolean; error?: string }[]
  succeeded: number
  failed: number
}

export interface ReuploadResult {
  status: 'success'
  message: string
  target: string
  image_uploaded: boolean
}

// ===== Config / settings =====

/** Every value is stored & returned as a string by the backend. */
export interface AppConfig {
  llm_provider: 'openai' | 'gemini'
  openai_api_key: string
  openai_model: string
  gemini_api_key: string
  gemini_model: string
  recipe_lang: string
  mealie_api_key: string
  mealie_host: string
  tandoor_api_key: string
  tandoor_host: string
  target_language: string
  /** String booleans! "true" / "false" */
  mealie_enabled: string
  tandoor_enabled: string
  whisper_model: string
  confirm_before_upload: string
  hf_token: string
  usda_fdc_api_key: string
  yt_dlp_cookies_file: string
  yt_dlp_cookies_browser: string
  max_concurrent_jobs: string
  [key: string]: string
}

export interface SettingsExport {
  version: string
  exported_at: string
  settings: AppConfig
}

export interface SessionUser {
  user: string
  is_admin: boolean
  auth_mode?: 'local' | 'authentik'
  /** False under Authentik, where the IdP owns accounts and passwords. */
  local_auth_enabled?: boolean
  /** Always false now; kept so an older cached bundle still parses a response. */
  auth_disabled?: boolean
  /** Popped server-side from the /share flow; consumed once by Home */
  shared_url?: string | null
  auto_start?: boolean
}

/** An account as /api/users exposes it. Never carries the password hash. */
export interface ManagedUser {
  username: string
  email?: string | null
  name?: string | null
  is_admin: boolean
  /** Came from the identity provider rather than being created here. */
  is_oidc: boolean
  has_password: boolean
  created_at?: string | null
}

// ===== Socket.IO events =====

export interface JobProgressPayload {
  job_id: string
  stage: PipelineStage
  message: string
  percent: number
  video_title: string | null
  queue_position: number
}

export interface JobCompletePayload {
  job_id: string
  recipe: RecipeData
  llm_tokens_used: number
}

export interface JobFailedPayload {
  job_id: string
  error: string
}

export interface JobTransitionPayload {
  job_id: string
  status: JobStatus
  previous_status: JobStatus
  reason: string | null
}

export interface ApprovalPayload {
  upload_id: string
  job_id: string
}

/** Dynamic per-state events: job_running, job_awaiting_approval, ... */
export type JobStateEvent =
  | `job_${Exclude<JobStatus, 'queued'>}`
  | 'job_queued'

export interface ServerToClientEvents {
  connected: (p: { status: string }) => void
  subscribed: (p: { job_id: string; status: string }) => void
  unsubscribed: (p: { job_id: string; status: string }) => void
  error: (p: { message: string }) => void
  recipe_preview: (p: {
    job_id: string
    upload_id: string | null
    recipe: RecipeData
    image_data: string | null
    candidate_images: CandidateImage[]
    best_image_index: number
    output_target: string
    owner: string | null
  }) => void
  job_progress: (p: JobProgressPayload) => void
  job_complete: (p: JobCompletePayload) => void
  job_failed: (p: JobFailedPayload) => void
  job_cancelled: (p: { job_id: string }) => void
  approval_confirmed: (p: ApprovalPayload) => void
  approval_rejected: (p: ApprovalPayload) => void
  approvals_updated: (p: { job_id: string }) => void
}

export interface ClientToServerEvents {
  subscribe_job: (
    jobId: string,
    ack?: (p: { job_id: string; status: string }) => void,
  ) => void
  unsubscribe_job: (
    jobId: string,
    ack?: (p: { job_id: string; status: string }) => void,
  ) => void
  confirm_upload: (uploadId: string, selectedIndex?: number | null) => void
  cancel_upload: (uploadId: string) => void
}
