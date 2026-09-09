import { Link } from 'react-router'
import {
  InfoIcon,
  DownloadIcon,
  MicIcon,
  EyeIcon,
  ImageIcon,
  BotIcon,
  CloudUploadIcon,
  XIcon,
  AlertCircleIcon,
  RotateCcwIcon,
} from 'lucide-react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import type { Job, JobStatus, PipelineStage } from '@/types'
import { PIPELINE_STAGES } from '@/types'

const STAGE_ICONS: Record<string, React.ReactNode> = {
  info: <InfoIcon className="size-3.5" />,
  download: <DownloadIcon className="size-3.5" />,
  transcribe: <MicIcon className="size-3.5" />,
  visual: <EyeIcon className="size-3.5" />,
  image: <ImageIcon className="size-3.5" />,
  evaluate: <BotIcon className="size-3.5" />,
  upload: <CloudUploadIcon className="size-3.5" />,
}

const STAGE_LABELS: Record<string, string> = {
  info: 'Info',
  download: 'Download',
  transcribe: 'Transcribe',
  visual: 'Visual',
  image: 'Image',
  evaluate: 'Evaluate',
  upload: 'Upload',
}

function statusBadgeVariant(
  status: JobStatus,
): React.ComponentProps<typeof Badge>['variant'] {
  switch (status) {
    case 'queued':
      return 'secondary'
    case 'running':
    case 'uploading':
      return 'default'
    case 'failed':
      return 'destructive'
    case 'cancelled':
    case 'expired':
      return 'outline'
    default:
      return 'secondary'
  }
}

function statusLabel(status: JobStatus): string {
  switch (status) {
    case 'queued': return 'Queued'
    case 'running': return 'Running'
    case 'awaiting_approval': return 'Awaiting Approval'
    case 'uploading': return 'Uploading'
    case 'completed': return 'Completed'
    case 'failed': return 'Failed'
    case 'cancelled': return 'Cancelled'
    case 'expired': return 'Expired'
  }
}

function isCancellable(status: JobStatus): boolean {
  return (
    status === 'queued' ||
    status === 'running' ||
    status === 'awaiting_approval' ||
    status === 'uploading'
  )
}

function resolvedDisplayStage(stage: PipelineStage | null): string {
  if (!stage) return ''
  if (stage === 'preview') return 'upload'
  return stage
}

interface StageTrackerProps {
  currentStage: PipelineStage | null
  status: JobStatus
}

function StageTracker({ currentStage, status }: StageTrackerProps) {
  const displayStage = resolvedDisplayStage(currentStage)
  const currentIndex = PIPELINE_STAGES.indexOf(displayStage as PipelineStage)

  return (
    <div className="flex items-center gap-1">
      {PIPELINE_STAGES.map((stage, index) => {
        const isCompleted =
          status === 'completed' ||
          (status !== 'failed' && index < currentIndex)
        const isActive = index === currentIndex && status !== 'failed' && status !== 'completed'
        const isError = status === 'failed' && index <= currentIndex && currentIndex >= 0

        return (
          <div
            key={stage}
            title={STAGE_LABELS[stage]}
            className={cn(
              'flex size-6 items-center justify-center rounded-full transition-colors',
              isError
                ? 'bg-destructive/20 text-destructive'
                : isCompleted
                  ? 'bg-primary/20 text-primary'
                  : isActive
                    ? 'bg-primary text-primary-foreground ring-2 ring-primary/30'
                    : 'bg-muted text-muted-foreground/50',
            )}
          >
            {STAGE_ICONS[stage]}
          </div>
        )
      })}
    </div>
  )
}

interface JobCardProps {
  job: Job
  onCancel?: (id: string) => void
  onRetry?: (job: Job) => void
  href?: string
}

export function JobCard({ job, onCancel, onRetry, href }: JobCardProps) {
  const title = job.video_title ?? truncateUrl(job.url)
  const cancellable = isCancellable(job.status)

  const messageText = (() => {
    if (job.status === 'queued' && job.queue_position) {
      return `Queued — position ${job.queue_position}`
    }
    return job.stage_message ?? statusLabel(job.status)
  })()

  return (
    <Card>
      <CardHeader>
        <div className="flex min-w-0 flex-col gap-0.5">
          <CardTitle className="truncate">
            {href ? (
              <Link to={href} className="hover:underline">
                {title}
              </Link>
            ) : (
              title
            )}
          </CardTitle>
          <CardDescription className="truncate text-xs">{job.url}</CardDescription>
        </div>
        <CardAction className="flex items-start gap-1.5">
          {job.status === 'queued' && job.queue_position != null && (
            <Badge variant="secondary" className="shrink-0">
              #{job.queue_position}
            </Badge>
          )}
          <Badge
            variant={statusBadgeVariant(job.status)}
            className={cn(
              'shrink-0',
              job.status === 'awaiting_approval' &&
                'border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400',
              job.status === 'completed' &&
                'border-emerald-500/50 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
            )}
          >
            {statusLabel(job.status)}
          </Badge>
          {cancellable && onCancel && (
            <Button
              variant="destructive"
              size="icon-sm"
              onClick={() => onCancel(job.id)}
              title="Cancel job"
            >
              <XIcon />
              <span className="sr-only">Cancel</span>
            </Button>
          )}
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-3">
        <StageTracker currentStage={job.current_stage} status={job.status} />

        <div className="space-y-1">
          <Progress value={job.progress} className="h-1.5" />
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="truncate">{messageText}</span>
            <span className="shrink-0 pl-2">{job.progress}%</span>
          </div>
        </div>

        {job.status === 'failed' && (
          <div className="space-y-2">
            {job.error_message && (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
                <span>{job.error_message}</span>
              </div>
            )}
            {onRetry && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onRetry(job)}
              >
                <RotateCcwIcon />
                Retry
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function truncateUrl(url: string): string {
  try {
    const parsed = new URL(url)
    const path = parsed.pathname.slice(0, 24)
    return parsed.hostname + path + (parsed.pathname.length > 24 ? '…' : '')
  } catch {
    return url.slice(0, 40) + (url.length > 40 ? '…' : '')
  }
}
