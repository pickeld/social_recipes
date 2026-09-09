import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  CheckIcon,
  XIcon,
  BanIcon,
  Trash2Icon,
  RefreshCwIcon,
  SearchIcon,
  MoreVerticalIcon,
  EyeIcon,
  UploadCloudIcon,
  RotateCcwIcon,
  ArrowUpIcon,
  ArrowDownIcon,
  InboxIcon,
  UtensilsIcon,
  BookIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useSocketEvent } from '@/lib/socket'
import { useSession } from '@/hooks/use-session'
import type {
  Job,
  CombinedItem,
  PendingUpload,
  CandidateImage,
  BulkAction,
  HistoryEntry,
  RecipeData,
} from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from '@/components/ui/dropdown-menu'
import { RecipeView } from '@/components/recipe-view'
import { RecipeEditForm } from '@/components/recipe-edit-form'
import { ImagePicker } from '@/components/image-picker'
import { cn } from '@/lib/utils'

// ===== Row model =====

type RowBucket = 'approval' | 'running' | 'queued' | 'done' | 'failed' | 'cancelled'
type RowKind = 'job' | 'history'

interface TaskRow {
  kind: RowKind
  key: string
  jobId: string | null
  historyId: number | null
  status: string
  bucket: RowBucket
  title: string
  url: string
  message: string
  error: string
  progress: number
  thumbnailData: string | null
  outputTarget: string | null
  pendingUploadId: string | null
  approvalExpiresAt: string
  queuePosition: number | null
  queuePriority: number
  createdAt: string
  updatedAt: string
}

const TERMINAL_STATUSES = new Set(['success', 'completed', 'failed', 'cancelled', 'expired'])

const BUCKET_ORDER: Record<RowBucket, number> = {
  approval: 0,
  running: 1,
  queued: 2,
  done: 3,
  failed: 3,
  cancelled: 3,
}

const GROUP_LABEL: Record<RowBucket, string> = {
  approval: 'Needs you',
  running: 'In flight',
  queued: 'In flight',
  done: 'Settled',
  failed: 'Settled',
  cancelled: 'Settled',
}

const STATUS_DISPLAY: Record<string, { label: string; variant: React.ComponentProps<typeof Badge>['variant']; className?: string }> = {
  queued:            { label: 'Queued',          variant: 'secondary' },
  pending:           { label: 'Queued',          variant: 'secondary' },
  running:           { label: 'Processing',      variant: 'default' },
  downloading:       { label: 'Downloading',     variant: 'default' },
  transcribing:      { label: 'Transcribing',    variant: 'default' },
  extracting:        { label: 'Extracting',      variant: 'default' },
  creating:          { label: 'Creating recipe', variant: 'default' },
  processing:        { label: 'Processing',      variant: 'default' },
  uploading:         { label: 'Uploading',       variant: 'default' },
  awaiting_approval: { label: 'Needs approval',  variant: 'outline', className: 'border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  success:           { label: 'Done',            variant: 'outline', className: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
  completed:         { label: 'Done',            variant: 'outline', className: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
  failed:            { label: 'Failed',          variant: 'destructive' },
  cancelled:         { label: 'Cancelled',       variant: 'outline' },
  expired:           { label: 'Expired',         variant: 'outline' },
}

type FilterBucket = '' | 'approval' | 'running' | 'queued' | 'done' | 'failed' | 'cancelled'

// ===== Normalization =====

function hostOf(url: string): string {
  if (!url) return ''
  try { return new URL(url).hostname.replace(/^www\./, '') }
  catch { return '' }
}

function bucketOf(status: string): RowBucket {
  if (status === 'awaiting_approval') return 'approval'
  if (status === 'queued' || status === 'pending') return 'queued'
  if (status === 'success' || status === 'completed') return 'done'
  if (status === 'failed') return 'failed'
  if (status === 'cancelled' || status === 'expired') return 'cancelled'
  return 'running'
}

function tsOf(row: TaskRow): number {
  const t = Date.parse(String(row.updatedAt || row.createdAt || '').replace(' ', 'T'))
  return isNaN(t) ? 0 : t
}

function normalizeJob(t: Job): TaskRow {
  return {
    kind: 'job',
    key: String(t.id),
    jobId: String(t.id),
    historyId: null,
    status: t.status,
    bucket: bucketOf(t.status),
    title: t.video_title || hostOf(t.url) || 'Untitled',
    url: t.url || '',
    message: t.stage_message || '',
    error: t.error_message || '',
    progress: t.progress || 0,
    thumbnailData: null,
    outputTarget: null,
    pendingUploadId: t.pending_upload_id ?? null,
    approvalExpiresAt: t.approval_expires_at ?? '',
    queuePosition: t.queue_position ?? null,
    queuePriority: t.queue_priority || 0,
    createdAt: t.created_at || '',
    updatedAt: t.updated_at || '',
  }
}

function normalizeHistory(r: CombinedItem): TaskRow {
  return {
    kind: 'history',
    key: 'h' + r.id,
    jobId: r.job_id,
    historyId: r.id,
    status: r.status,
    bucket: bucketOf(r.status),
    title: r.recipe_name || r.video_title || hostOf(r.url) || 'Untitled Recipe',
    url: r.url || '',
    message: '',
    error: r.error_message || '',
    progress: 0,
    thumbnailData: r.thumbnail_data || null,
    outputTarget: r.output_target || null,
    pendingUploadId: null,
    approvalExpiresAt: '',
    queuePosition: null,
    queuePriority: 0,
    createdAt: r.created_at || '',
    updatedAt: r.updated_at || r.created_at || '',
  }
}

function mergeRows(jobs: Job[], recipes: CombinedItem[]): TaskRow[] {
  const byJobId = new Map(jobs.map((t) => [String(t.id), t]))

  const superseded = new Set(
    jobs
      .filter((t) => !TERMINAL_STATUSES.has(t.status) && t.retry_from_history_id != null)
      .map((t) => String(t.retry_from_history_id)),
  )

  const rows: TaskRow[] = []
  for (const r of recipes) {
    if (r.source_type !== 'history') continue
    const twinKey = r.job_id ? String(r.job_id) : null
    if (superseded.has(String(r.id))) {
      if (twinKey) byJobId.delete(twinKey)
      continue
    }
    if (twinKey) byJobId.delete(twinKey)
    rows.push(normalizeHistory(r))
  }
  byJobId.forEach((t) => rows.push(normalizeJob(t)))

  rows.sort((a, b) => {
    const wa = BUCKET_ORDER[a.bucket] ?? 9
    const wb = BUCKET_ORDER[b.bucket] ?? 9
    if (wa !== wb) return wa - wb
    return tsOf(b) - tsOf(a)
  })

  return rows
}

// ===== Relative time =====

function relativeTime(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(String(dateStr).replace(' ', 'T'))
  const diff = Date.now() - date.getTime()
  if (isNaN(diff)) return ''
  if (diff < 60000) return 'just now'
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago'
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago'
  if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago'
  return date.toLocaleDateString()
}

// ===== Expiry countdown =====

function useExpiryCountdown(isoStr: string): { text: string; expiring: boolean } {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  if (!isoStr) return { text: '', expiring: false }
  const ms = new Date(isoStr.replace(' ', 'T') + (isoStr.endsWith('Z') ? '' : 'Z')).getTime() - now
  if (isNaN(ms)) return { text: '', expiring: false }
  if (ms <= 0) return { text: 'expired', expiring: true }
  const m = Math.floor(ms / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  return { text: `${m}:${String(s).padStart(2, '0')}`, expiring: ms < 60000 }
}

// ===== Status badge =====

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_DISPLAY[status] ?? { label: status, variant: 'outline' as const }
  return (
    <Badge variant={meta.variant} className={meta.className}>
      {meta.label}
    </Badge>
  )
}

// ===== Approval gallery row =====

interface ApprovalGalleryProps {
  uploadId: string
  onTitleResolved: (title: string) => void
  selectedIndex: number
  onSelectIndex: (i: number) => void
}

function ApprovalGallery({ uploadId, onTitleResolved, selectedIndex, onSelectIndex }: ApprovalGalleryProps) {
  const { data } = useQuery<PendingUpload>({
    queryKey: ['pending-upload', uploadId],
    queryFn: () => api.getPendingUpload(uploadId),
    staleTime: 30000,
  })

  useEffect(() => {
    if (data?.recipe?.name) onTitleResolved(data.recipe.name)
  }, [data, onTitleResolved])

  const candidates: CandidateImage[] = useMemo(() => {
    if (!data) return []
    const list = (data.candidate_images || []).filter((c) => c.data)
    if (!list.length && data.image_data) {
      return [{ index: 0, data: data.image_data, path: '', is_best: true }]
    }
    return list
  }, [data])

  const summaryBits: string[] = []
  if (data?.recipe) {
    const r = data.recipe
    const ingCount = (r.recipeIngredient?.length ?? 0) + (r.recipeIngredients?.length ?? 0)
    if (ingCount) summaryBits.push(`${ingCount} ingredients`)
    if (r.recipeInstructions?.length) summaryBits.push(`${r.recipeInstructions.length} steps`)
  }

  return (
    <div className="mt-2 space-y-2">
      {summaryBits.length > 0 && (
        <p className="text-xs text-muted-foreground">{summaryBits.join(' · ')}</p>
      )}
      {candidates.length > 0 && (
        <ImagePicker images={candidates} value={selectedIndex} onChange={onSelectIndex} />
      )}
    </div>
  )
}

// ===== Per-row expiry display =====

function ExpiryChip({ isoStr }: { isoStr: string }) {
  const { text, expiring } = useExpiryCountdown(isoStr)
  if (!text) return null
  return (
    <span className={cn('font-mono text-xs tabular-nums', expiring && 'text-destructive')}>
      {text}
    </span>
  )
}

// ===== Delete confirmation dialogs =====

interface DeleteRowDialogProps {
  row: TaskRow | null
  onClose: () => void
  onConfirm: () => void
}

function DeleteRowDialog({ row, onClose, onConfirm }: DeleteRowDialogProps) {
  return (
    <AlertDialog open={row !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete item?</AlertDialogTitle>
          <AlertDialogDescription>
            This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>Delete</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

interface BulkDeleteDialogProps {
  count: number
  open: boolean
  onClose: () => void
  onConfirm: () => void
}

function BulkDeleteDialog({ count, open, onClose, onConfirm }: BulkDeleteDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {count} items?</AlertDialogTitle>
          <AlertDialogDescription>
            This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>Delete all</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ===== Recipe detail modal =====

interface RecipeModalProps {
  historyId: number | null
  onClose: () => void
  onDelete: (historyId: number) => void
}

function RecipeModal({ historyId, onClose, onDelete }: RecipeModalProps) {
  const { data, isLoading } = useQuery<HistoryEntry>({
    queryKey: ['history-item', historyId],
    queryFn: () => api.getHistoryItem(historyId!),
    enabled: historyId !== null,
  })

  const [reuploadPending, setReuploadPending] = useState(false)

  const handleReupload = useCallback(async (target: string) => {
    if (!data) return
    setReuploadPending(true)
    try {
      await api.reuploadRecipe(data.id, target)
      toast.success(`Recipe re-uploaded to ${target}!`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Re-upload failed')
    } finally {
      setReuploadPending(false)
    }
  }, [data])

  const canReupload = data?.status === 'success' && data.recipe_data != null

  return (
    <Dialog open={historyId !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>
            {isLoading ? 'Loading…' : (data?.recipe_name || data?.video_title || 'Recipe Details')}
          </DialogTitle>
        </DialogHeader>

        {isLoading && (
          <div className="space-y-3 py-2">
            <Skeleton className="h-48 w-full rounded-lg" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}

        {!isLoading && data && (
          <div className="flex-1 overflow-y-auto space-y-3 pr-1">
            {data.thumbnail_data && (
              <img
                src={`data:image/jpeg;base64,${data.thumbnail_data}`}
                alt={data.recipe_name ?? 'Recipe'}
                className="w-full rounded-lg object-cover max-h-48"
              />
            )}
            <div className="text-xs text-muted-foreground space-y-1">
              {data.url && (
                <p>
                  <a
                    href={data.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-2 hover:text-foreground"
                  >
                    {data.url}
                  </a>
                </p>
              )}
              <p>Created: {new Date(String(data.created_at).replace(' ', 'T')).toLocaleString()}</p>
              {data.output_target && <p>Uploaded to: {data.output_target}</p>}
            </div>
            {data.recipe_data && <RecipeView recipe={data.recipe_data} />}
          </div>
        )}

        <DialogFooter>
          {data && (
            <>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => onDelete(data.id)}
              >
                <Trash2Icon />
                Delete
              </Button>
              {canReupload && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="sm" disabled={reuploadPending}>
                      <UploadCloudIcon />
                      Re-upload
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => handleReupload('tandoor')}>
                      <UtensilsIcon />
                      Tandoor
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleReupload('mealie')}>
                      <BookIcon />
                      Mealie
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface ApprovalReviewDialogProps {
  uploadId: string | null
  initialImageIndex: number
  onClose: () => void
  onConfirmed: () => void
}

function ApprovalReviewDialog({
  uploadId,
  initialImageIndex,
  onClose,
  onConfirmed,
}: ApprovalReviewDialogProps) {
  const { data, isLoading } = useQuery<PendingUpload>({
    queryKey: ['pending-upload', uploadId],
    queryFn: () => api.getPendingUpload(uploadId!),
    enabled: uploadId !== null,
  })
  const [selectedIndex, setSelectedIndex] = useState(initialImageIndex)
  const [recipe, setRecipe] = useState<RecipeData | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setSelectedIndex(initialImageIndex)
    setRecipe(null)
  }, [uploadId, initialImageIndex])

  useEffect(() => {
    if (data?.recipe && recipe === null) {
      setRecipe(structuredClone(data.recipe))
    }
  }, [data, recipe])

  const candidates: CandidateImage[] = useMemo(() => {
    if (!data) return []
    const list = (data.candidate_images || []).filter((c) => c.data)
    if (!list.length && data.image_data) {
      return [{ index: 0, data: data.image_data, path: '', is_best: true }]
    }
    return list
  }, [data])

  const handleConfirm = useCallback(async () => {
    if (!uploadId || !recipe) return
    setSaving(true)
    try {
      await api.confirmPendingUpload(uploadId, {
        selected_image_index: selectedIndex,
        recipe,
      })
      toast.success('Approved — uploading…')
      onConfirmed()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Approve failed')
    } finally {
      setSaving(false)
    }
  }, [uploadId, recipe, selectedIndex, onConfirmed])

  return (
    <Dialog open={uploadId !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Review recipe</DialogTitle>
          <DialogDescription>
            Edit the recipe, then confirm to upload
            {data?.output_target ? ` to ${data.output_target}` : ''}.
          </DialogDescription>
        </DialogHeader>
        {isLoading && (
          <div className="space-y-3 py-2">
            <Skeleton className="h-24 w-full rounded-lg" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        )}
        {!isLoading && recipe && (
          <div className="space-y-4">
            {candidates.length > 0 && (
              <ImagePicker
                images={candidates}
                value={selectedIndex}
                onChange={setSelectedIndex}
              />
            )}
            <RecipeEditForm recipe={recipe} onChange={setRecipe} />
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void handleConfirm()} disabled={saving || !recipe}>
            Confirm upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ===== Task row =====

interface TaskRowProps {
  row: TaskRow
  selected: boolean
  onSelect: (key: string, checked: boolean) => void
  onReview: (uploadId: string, imageIndex: number) => void
  onReject: (uploadId: string) => void
  onRetry: (url: string, historyId: number) => void
  onCancelJob: (jobId: string) => void
  onPriorityShift: (jobId: string, delta: number) => void
  onViewRecipe: (historyId: number) => void
  onDeleteRow: (row: TaskRow) => void
  onReupload: (historyId: number, target: string) => void
}

function TaskRowItem({
  row,
  selected,
  onSelect,
  onReview,
  onReject,
  onRetry,
  onCancelJob,
  onPriorityShift,
  onViewRecipe,
  onDeleteRow,
  onReupload,
}: TaskRowProps) {
  const isTerminal = TERMINAL_STATUSES.has(row.status)
  const isApproval = row.bucket === 'approval'
  const isQueued = row.bucket === 'queued'
  const isActive = row.status === 'running' || row.status === 'uploading'
  const isSuccess = row.kind === 'history' && row.status === 'success'

  const [selectedImageIndex, setSelectedImageIndex] = useState(0)
  const [resolvedTitle, setResolvedTitle] = useState<string | null>(null)

  const displayTitle = resolvedTitle ?? row.title

  const metaBits: string[] = [relativeTime(row.updatedAt || row.createdAt)]
  if (row.bucket === 'done' && row.outputTarget) metaBits.push('→ ' + row.outputTarget)
  const h = hostOf(row.url)
  if (h && row.bucket === 'done') metaBits.push(h)
  if (row.kind === 'history' && row.status === 'failed' && row.error) {
    metaBits.push(row.error.length > 80 ? row.error.slice(0, 80) + '…' : row.error)
  }

  const handleRowClick = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('input, button, [role="menuitem"], [role="menu"]')) return
    if (isSuccess) onViewRecipe(row.historyId!)
  }, [isSuccess, row.historyId, onViewRecipe])

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-lg border bg-card px-3 py-3 transition-colors',
        isSuccess && 'cursor-pointer hover:bg-muted/50',
        isApproval && 'border-amber-500/30 bg-amber-500/5',
      )}
      onClick={handleRowClick}
    >
      <input
        type="checkbox"
        aria-label="Select task"
        checked={selected}
        className="mt-1 shrink-0 accent-primary"
        onChange={(e) => onSelect(row.key, e.target.checked)}
      />

      {row.thumbnailData ? (
        <img
          src={`data:image/jpeg;base64,${row.thumbnailData}`}
          alt=""
          className="size-12 shrink-0 rounded-md object-cover"
        />
      ) : (
        <div className="size-12 shrink-0 rounded-md bg-muted" />
      )}

      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge status={row.status} />
          <span className="font-medium text-sm truncate max-w-xs">{displayTitle}</span>
          {isQueued && row.queuePosition != null && (
            <Badge variant="secondary" className="font-mono shrink-0">
              #{row.queuePosition}
            </Badge>
          )}
          {isApproval && row.approvalExpiresAt && (
            <ExpiryChip isoStr={row.approvalExpiresAt} />
          )}
        </div>

        {isApproval && row.pendingUploadId && (
          <ApprovalGallery
            uploadId={row.pendingUploadId}
            onTitleResolved={setResolvedTitle}
            selectedIndex={selectedImageIndex}
            onSelectIndex={setSelectedImageIndex}
          />
        )}

        {(isActive || (row.kind === 'job' && row.progress > 0)) && (
          <div className="flex items-center gap-2">
            <Progress value={row.progress} className="h-1.5 flex-1" />
            <span className="font-mono text-xs tabular-nums text-muted-foreground shrink-0">
              {row.progress}%
            </span>
          </div>
        )}

        {metaBits.filter(Boolean).length > 0 && (
          <p className="text-xs text-muted-foreground font-mono truncate">
            {metaBits.filter(Boolean).join(' · ')}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
        {isApproval && row.pendingUploadId && (
          <Button
            size="sm"
            onClick={() => onReview(row.pendingUploadId!, selectedImageIndex)}
          >
            <CheckIcon />
            Approve
          </Button>
        )}
        {row.status === 'failed' && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              if (row.kind === 'history' && row.historyId != null) {
                onRetry(row.url, row.historyId)
              } else if (row.jobId) {
                onRetry(row.url, 0)
              }
            }}
          >
            <RotateCcwIcon />
            Retry
          </Button>
        )}
        {isSuccess && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onViewRecipe(row.historyId!)}
          >
            <EyeIcon />
            View
          </Button>
        )}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm">
              <MoreVerticalIcon />
              <span className="sr-only">More actions</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {isSuccess && (
              <DropdownMenuItem onClick={() => onViewRecipe(row.historyId!)}>
                <EyeIcon />
                View recipe
              </DropdownMenuItem>
            )}
            {isSuccess && (
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>
                  <UploadCloudIcon />
                  Re-upload
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  <DropdownMenuItem onClick={() => onReupload(row.historyId!, 'tandoor')}>
                    <UtensilsIcon />
                    Tandoor
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => onReupload(row.historyId!, 'mealie')}>
                    <BookIcon />
                    Mealie
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            )}
            {isApproval && row.pendingUploadId && (
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onReject(row.pendingUploadId!)}
              >
                <XIcon />
                Reject
              </DropdownMenuItem>
            )}
            {isQueued && row.jobId && (
              <>
                <DropdownMenuItem onClick={() => onPriorityShift(row.jobId!, +1)}>
                  <ArrowUpIcon />
                  Move earlier
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onPriorityShift(row.jobId!, -1)}>
                  <ArrowDownIcon />
                  Move later
                </DropdownMenuItem>
              </>
            )}
            {!isTerminal && !isApproval && row.jobId && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => onCancelJob(row.jobId!)}
                >
                  <BanIcon />
                  Cancel job
                </DropdownMenuItem>
              </>
            )}
            {isTerminal && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => onDeleteRow(row)}
                >
                  <Trash2Icon />
                  Delete
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

// ===== Skeleton loading rows =====

function RowSkeleton() {
  return (
    <div className="flex items-start gap-3 rounded-lg border bg-card px-3 py-3">
      <Skeleton className="mt-1 size-4 rounded shrink-0" />
      <Skeleton className="size-12 rounded-md shrink-0" />
      <div className="flex-1 space-y-2">
        <div className="flex gap-2">
          <Skeleton className="h-5 w-20 rounded-full" />
          <Skeleton className="h-5 w-40" />
        </div>
        <Skeleton className="h-3 w-48" />
      </div>
    </div>
  )
}

// ===== Group header =====

function GroupHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-2 pt-2 pb-1 first:pt-0">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-xs text-muted-foreground tabular-nums">{count}</span>
      <div className="flex-1 h-px bg-border" />
    </div>
  )
}

// ===== Main page =====

const FILTER_OPTIONS: { value: FilterBucket; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'approval', label: 'Needs approval' },
  { value: 'running', label: 'Running' },
  { value: 'queued', label: 'Queued' },
  { value: 'done', label: 'Done' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
]

export function TasksPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { data: session } = useSession()

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<FilterBucket>('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleteRow, setDeleteRow] = useState<TaskRow | null>(null)
  const [showBulkDelete, setShowBulkDelete] = useState(false)
  const [recipeModalId, setRecipeModalId] = useState<number | null>(null)
  const [deleteAfterCloseId, setDeleteAfterCloseId] = useState<number | null>(null)
  const [reviewState, setReviewState] = useState<{
    uploadId: string
    imageIndex: number
  } | null>(null)

  const scope = session?.is_admin ? 'all' : 'mine'

  const { data: tasksData, isLoading: tasksLoading } = useQuery({
    queryKey: ['tasks', scope],
    queryFn: () => api.listTasks({ state: 'all', scope, limit: 300 }),
    refetchInterval: 15000,
  })

  const { data: recipesData, isLoading: recipesLoading } = useQuery({
    queryKey: ['recipes'],
    queryFn: () => api.getRecipes({ limit: 300 }),
    refetchInterval: 15000,
  })

  const isLoading = tasksLoading || recipesLoading

  const rows = useMemo<TaskRow[]>(() => {
    if (!tasksData || !recipesData) return []
    return mergeRows(tasksData.tasks || [], recipesData.items || [])
  }, [tasksData, recipesData])

  const counts = tasksData?.counts ?? {}

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    void queryClient.invalidateQueries({ queryKey: ['recipes'] })
  }, [queryClient])

  const socketInvalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    void queryClient.invalidateQueries({ queryKey: ['recipes'] })
  }, [queryClient])

  useSocketEvent('job_progress', socketInvalidate)
  useSocketEvent('job_complete', socketInvalidate)
  useSocketEvent('job_failed', socketInvalidate)
  useSocketEvent('job_cancelled', socketInvalidate)
  useSocketEvent('approval_confirmed', socketInvalidate)
  useSocketEvent('approval_rejected', socketInvalidate)
  useSocketEvent('approvals_updated', socketInvalidate)

  const visibleRows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows.filter((r) => {
      if (filter && r.bucket !== filter) return false
      if (!q) return true
      return r.title.toLowerCase().includes(q) || r.url.toLowerCase().includes(q)
    })
  }, [rows, filter, search])

  const grouped = useMemo(() => {
    const groups: { label: string; rows: TaskRow[] }[] = []
    let current: { label: string; rows: TaskRow[] } | null = null
    for (const row of visibleRows) {
      const label = GROUP_LABEL[row.bucket]
      if (!current || current.label !== label) {
        current = { label, rows: [] }
        groups.push(current)
      }
      current.rows.push(row)
    }
    return groups
  }, [visibleRows])

  const handleSelect = useCallback((key: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(key)
      else next.delete(key)
      return next
    })
  }, [])

  const handleSelectAll = useCallback((checked: boolean) => {
    if (checked) {
      setSelected(new Set(visibleRows.map((r) => r.key)))
    } else {
      setSelected(new Set())
    }
  }, [visibleRows])

  const rowByKey = useCallback((key: string) => rows.find((r) => r.key === key) ?? null, [rows])

  const selectedRows = useMemo(
    () => Array.from(selected).map((k) => rowByKey(k)).filter((r): r is TaskRow => r !== null),
    [selected, rowByKey],
  )

  const hasApproval = selectedRows.some((r) => r.bucket === 'approval')
  const hasCancellable = selectedRows.some(
    (r) => !TERMINAL_STATUSES.has(r.status) && r.bucket !== 'approval',
  )

  const handleReview = useCallback((uploadId: string, imageIndex: number) => {
    setReviewState({ uploadId, imageIndex })
  }, [])

  const handleReject = useCallback(async (uploadId: string) => {
    try {
      await api.cancelPendingUpload(uploadId)
      toast.success('Rejected')
      invalidate()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Reject failed')
    }
  }, [invalidate])

  const handleCancelJob = useCallback(async (jobId: string) => {
    try {
      await api.cancelJob(jobId)
      invalidate()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Cancel failed')
    }
  }, [invalidate])

  const handlePriorityShift = useCallback(async (jobId: string, delta: number) => {
    const row = rows.find((r) => r.jobId === jobId)
    const current = Number(row?.queuePriority ?? 0)
    try {
      await api.setJobPriority(jobId, current + delta)
      invalidate()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Priority update failed')
    }
  }, [rows, invalidate])

  const handleRetry = useCallback(async (url: string, historyId: number) => {
    try {
      const data = await api.retryJob({ url, history_id: historyId || undefined })
      toast.success('Retry started!')
      void navigate(`/jobs/${data.job_id}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Retry failed')
    }
  }, [navigate])

  const handleReupload = useCallback(async (historyId: number, target: string) => {
    try {
      await api.reuploadRecipe(historyId, target)
      toast.success(`Recipe re-uploaded to ${target}!`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Re-upload failed')
    }
  }, [])

  const handleDeleteRow = useCallback(async (row: TaskRow) => {
    try {
      if (row.kind === 'history' && row.historyId != null) {
        await api.deleteHistoryItem(row.historyId)
      } else if (row.jobId) {
        await api.deleteJobEntry(row.jobId)
      }
      toast.success('Item deleted')
      invalidate()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed')
    }
  }, [invalidate])

  const handleDeleteConfirm = useCallback(() => {
    if (!deleteRow) return
    void handleDeleteRow(deleteRow).then(() => setDeleteRow(null))
  }, [deleteRow, handleDeleteRow])

  const handleBulkAction = useCallback(async (action: BulkAction) => {
    const ids = Array.from(selected).filter((k) => !k.startsWith('h'))
    if (!ids.length) return
    try {
      const res = await api.bulkTaskAction(action, ids)
      toast.success(`${res.succeeded} ${action}d, ${res.failed} skipped`)
      setSelected(new Set())
      invalidate()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Bulk action failed')
    }
  }, [selected, invalidate])

  const handleBulkDelete = useCallback(async () => {
    const historyIds: number[] = []
    const jobIds: string[] = []
    selected.forEach((key) => {
      if (key.startsWith('h')) historyIds.push(parseInt(key.slice(1), 10))
      else jobIds.push(key)
    })
    if (!historyIds.length && !jobIds.length) return
    try {
      const data = await api.bulkDelete({ history_ids: historyIds, job_ids: jobIds })
      toast.success(`Deleted ${data.deleted_count} items`)
      setSelected(new Set())
      invalidate()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Bulk delete failed')
    }
  }, [selected, invalidate])

  const handleRecipeDelete = useCallback((historyId: number) => {
    setDeleteAfterCloseId(historyId)
    setRecipeModalId(null)
  }, [])

  const prevRecipeModalId = useRef<number | null>(null)
  useEffect(() => {
    if (prevRecipeModalId.current !== null && recipeModalId === null && deleteAfterCloseId !== null) {
      const row = rows.find((r) => r.historyId === deleteAfterCloseId) ?? null
      if (row) setDeleteRow(row)
      setDeleteAfterCloseId(null)
    }
    prevRecipeModalId.current = recipeModalId
  }, [recipeModalId, deleteAfterCloseId, rows])

  const allVisibleSelected = visibleRows.length > 0 && visibleRows.every((r) => selected.has(r.key))
  const someVisibleSelected = visibleRows.some((r) => selected.has(r.key))

  const approvalCount = counts['awaiting_approval'] ?? 0

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <div>
        <h1 className="text-xl font-semibold">Tasks</h1>
        <p className="text-sm text-muted-foreground">
          Live jobs up top — everything that finished settles below as Done
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-48">
          <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
          <Input
            placeholder="Search tasks…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>

        <div className="flex flex-wrap gap-1">
          {FILTER_OPTIONS.map((opt) => (
            <Button
              key={opt.value}
              size="sm"
              variant={filter === opt.value ? 'default' : 'outline'}
              onClick={() => setFilter(opt.value)}
              className="relative"
            >
              {opt.label}
              {opt.value === 'approval' && approvalCount > 0 && (
                <span className="ml-1 rounded-full bg-amber-500 px-1 text-xs text-white">
                  {approvalCount}
                </span>
              )}
            </Button>
          ))}
        </div>

        {rows.length > 0 && (
          <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
            <input
              type="checkbox"
              checked={allVisibleSelected}
              ref={(el) => {
                if (el) el.indeterminate = someVisibleSelected && !allVisibleSelected
              }}
              onChange={(e) => handleSelectAll(e.target.checked)}
              className="accent-primary"
            />
            Select all
          </label>
        )}

        <Button size="sm" variant="outline" onClick={invalidate}>
          <RefreshCwIcon />
          Refresh
        </Button>
      </div>

      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2">
          <span className="text-sm font-medium">{selected.size} selected</span>
          {hasApproval && (
            <Button size="sm" onClick={() => void handleBulkAction('approve')}>
              <CheckIcon />
              Approve
            </Button>
          )}
          {hasApproval && (
            <Button size="sm" variant="destructive" onClick={() => void handleBulkAction('reject')}>
              <XIcon />
              Reject
            </Button>
          )}
          {hasCancellable && (
            <Button size="sm" variant="secondary" onClick={() => void handleBulkAction('cancel')}>
              <BanIcon />
              Cancel jobs
            </Button>
          )}
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setShowBulkDelete(true)}
          >
            <Trash2Icon />
            Delete
          </Button>
        </div>
      )}

      <div className="space-y-1">
        {isLoading && rows.length === 0 && (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <RowSkeleton key={i} />
            ))}
          </div>
        )}

        {!isLoading && visibleRows.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-16 text-muted-foreground">
            <InboxIcon className="size-10 opacity-40" />
            <p className="text-sm">
              {rows.length > 0
                ? 'Nothing matches the current search or filter.'
                : 'Nothing here right now.'}
            </p>
          </div>
        )}

        {grouped.map((group) => (
          <div key={group.label} className="space-y-1">
            <GroupHeader label={group.label} count={group.rows.length} />
            {group.rows.map((row) => (
              <TaskRowItem
                key={row.key}
                row={row}
                selected={selected.has(row.key)}
                onSelect={handleSelect}
                onReview={handleReview}
                onReject={handleReject}
                onRetry={handleRetry}
                onCancelJob={handleCancelJob}
                onPriorityShift={handlePriorityShift}
                onViewRecipe={setRecipeModalId}
                onDeleteRow={setDeleteRow}
                onReupload={handleReupload}
              />
            ))}
          </div>
        ))}
      </div>

      <RecipeModal
        historyId={recipeModalId}
        onClose={() => setRecipeModalId(null)}
        onDelete={handleRecipeDelete}
      />

      <ApprovalReviewDialog
        uploadId={reviewState?.uploadId ?? null}
        initialImageIndex={reviewState?.imageIndex ?? 0}
        onClose={() => setReviewState(null)}
        onConfirmed={() => {
          setReviewState(null)
          invalidate()
        }}
      />

      <DeleteRowDialog
        row={deleteRow}
        onClose={() => setDeleteRow(null)}
        onConfirm={handleDeleteConfirm}
      />

      <BulkDeleteDialog
        count={selected.size}
        open={showBulkDelete}
        onClose={() => setShowBulkDelete(false)}
        onConfirm={() => {
          setShowBulkDelete(false)
          void handleBulkDelete()
        }}
      />
    </div>
  )
}
